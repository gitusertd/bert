from typing import Optional

import tensorflow as tf
from tensorflow.python.eager import context
from tensorflow.python.keras.utils import tf_utils
from tensorflow.python.framework import ops
from tensorflow.python.ops import embedding_ops


class AdvancedEmbedding(tf.keras.layers.Layer):
    """Calculate token type ids and return the sum of subword-token, positional end token-type embeddings."""
    # TODO: maybe break down this class into the three distinct
    def __init__(self,
                 vocab_size: int = 119547,
                 token_type_vocab_size: int = 2,
                 sep_token_index: int = 102,
                 output_dim: int = 768,
                 use_one_hot_embedding: bool = False,  # currently is not used
                 max_len: int = 512,
                 initializer_range: float = 0.02,
                 trainable_pos_embedding: bool = True,  # always True in the original implementation
                 **kwargs) -> None:
        super().__init__(**kwargs)

        self.vocab_size = vocab_size
        self.token_type_vocab_size = token_type_vocab_size
        self.sep_token_index = sep_token_index
        self.output_dim = output_dim
        self.max_len = max_len
        self.embeddings_initializer = tf.keras.initializers.TruncatedNormal(stddev=initializer_range)
        self.trainable_pos_embedding = trainable_pos_embedding

    @tf_utils.shape_type_conversion
    def build(self, batch_input_shape):
        # Note: most sparse optimizers do not have GPU kernels defined. When
        # building graphs, the placement algorithm is able to place variables on CPU
        # since it knows all kernels using the variable only exist on CPU.
        # When eager execution is enabled, the placement decision has to be made
        # right now. Checking for the presence of GPUs to avoid complicating the
        # TPU codepaths which can handle sparse optimizers.
        if context.executing_eagerly() and context.context().num_gpus():
            with ops.device('cpu:0'):
                self._create_weights(batch_input_shape)
        else:
            self._create_weights(batch_input_shape)
        self.built = True

    def _create_weights(self, batch_input_shape):

        self.token_emb_table = self.add_weight(shape=(self.vocab_size, self.output_dim),
                                               dtype=tf.float32,
                                               initializer=self.embeddings_initializer,
                                               name='word_embeddings')

        self.token_type_emb_table = self.add_weight(shape=(self.token_type_vocab_size, self.output_dim),
                                                    dtype=tf.float32,
                                                    initializer=self.embeddings_initializer,
                                                    name='token_type_embeddings')

        # Since the position embedding table is a learned variable, we create it using a (long) sequence length
        # `max_len`. The actual sequence length might be shorter than this, for faster training of
        # tasks that do not have long sequences.
        self.full_position_emb_table = self.add_weight(shape=(self.max_len, self.output_dim),
                                                       dtype=tf.float32,
                                                       initializer=self.embeddings_initializer,
                                                       trainable=self.trainable_pos_embedding,
                                                       name='position_embeddings')

    def call(self,
             token_ids: tf.Tensor,
             training: Optional[bool] = None,
             mask: Optional[tf.Tensor] = None,
             **kwargs) -> tf.Tensor:

        token_emb = embedding_ops.embedding_lookup(self.token_emb_table, tf.cast(token_ids, tf.int32))

        # So `full_position_embeddings_table` is effectively an embedding table for position
        # [0, 1, 2, ..., max_position_embeddings-1], and the current sequence has positions [0, 1, 2, ... seq_length-1],
        # so we can just perform a slice.
        pos_emb = self.full_position_emb_table[:tf.shape(token_ids)[1], :]

        sep_ids = tf.cast(tf.equal(token_ids, self.sep_token_index), dtype=tf.int32)
        segment_ids = tf.cumsum(sep_ids, axis=1) - sep_ids
        # This vocab will be small so we always do one-hot here, since it is always
        # faster for a small vocabulary.
        flat_segment_ids = tf.reshape(segment_ids, [-1])
        oh_segment_ids = tf.one_hot(flat_segment_ids, depth=self.token_type_vocab_size)
        segment_emb = tf.matmul(oh_segment_ids, self.token_type_emb_table)
        segment_emb = tf.reshape(segment_emb, tf.shape(token_emb))

        return token_emb + pos_emb + segment_emb

    def compute_output_shape(self, input_shape):
        return input_shape[0], input_shape[1], self.output_dim

# kaggle比赛tweet_sentiment_extraction，带你上0.71分数

- 链接: https://zhuanlan.zhihu.com/p/139779541
- 发布: 2020-05-11 22:01:02
- 赞同: 17 | 评论: 19

---

## 1.前言

在这个比赛已经折腾了1个多月了，终于分数有点上升。在LB分数榜上可以取得 **0.71** 的分数，模型主要用到Roberta模型。主要使用的技术如下：

- **focal loss** ：解决类别不平衡问题
- **adversarial training** ：对抗训练
- **Roberta** ：基础模型
- **learing schedule** ：控制每次迭代的学习率，使用了warmup操作和指数下降操作。
- **get_best_start_end_idxs** ：函数方法，主要用来获取最佳的start位置和end位置
- 在neutral、positive和negative中统计了 **top 10** 经常出现的词语，并引入到模型中，作为辅助特征。

详细代码：

[https://github.com/llq20133100095/tweet_sentiment_extraction/blob/other_mission2/thinking/adversarial_training/tweet_roberta_adversail_training.ipynb](https://github.com/llq20133100095/tweet_sentiment_extraction/blob/other_mission2/thinking/adversarial_training/tweet_roberta_adversail_training.ipynb)

一些具体的建模方法，可以参考我之前写的几篇文章：

[withYou：BERT in tweet_sentiment_extraction](https://zhuanlan.zhihu.com/p/132400462)

[withYou：使用BERT的两层encoder实现tweet sentiment extraction](https://zhuanlan.zhihu.com/p/134599876)

[withYou：使用transformers和tensorflow2.0跑bert模型](https://zhuanlan.zhihu.com/p/137222913)

## 2.代码分析

## 2.1 加载必要的库

```python
import numpy as np
import pandas as pd
from math import ceil, floor
import tensorflow as tf
import tensorflow_probability as tfp
import tensorflow.keras.layers as L
from tensorflow.keras.initializers import TruncatedNormal
from sklearn import model_selection
from transformers import BertConfig, TFBertPreTrainedModel, TFBertMainLayer
from transformers import RobertaConfig, TFRobertaPreTrainedModel, TFRobertaMainLayer, TFRobertaModel
from tokenizers import BertWordPieceTokenizer, ByteLevelBPETokenizer
import matplotlib.pyplot as plt
from tqdm.autonotebook import tqdm
from joblib import Parallel, delayed
import os

import logging
tf.get_logger().setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")
```

主要是要加载tensorflow、joblib、sklearn、transformers等库。

## 2.2 加载比赛中的数据

```python
# read csv files
train_df = pd.read_csv('../input/tweet-sentiment-extraction/train.csv')
train_df.dropna(inplace=True)

test_df = pd.read_csv('../input/tweet-sentiment-extraction/test.csv')
test_df.loc[:, "selected_text"] = test_df.text.values

submission_df = pd.read_csv('../input/tweet-sentiment-extraction/sample_submission.csv')

print("train shape =", train_df.shape)
print("test shape  =", test_df.shape)

# set some global variables
PATH = "../input/tf-roberta/"
MAX_SEQUENCE_LENGTH = 96
# TOKENIZER = BertWordPieceTokenizer(f"../input/bert-base-uncased/vocab.txt", lowercase=True, add_special_tokens=False)
TOKENIZER = ByteLevelBPETokenizer(vocab_file=f"{PATH}/vocab-roberta-base.json", 
                                  merges_file=f"{PATH}/merges-roberta-base.txt", 
                                  lowercase=True, 
                                  add_prefix_space=True)

sentiment_dict = {"positive": ["good", "happy", "love", "day", "thanks", "great", "fun", "nice", "hope", "thank"],
                  "negative": ["miss", "sad", "sorry", "bad", "hate", "sucks", "sick", "like", "feel", "bored"],
                  "neutral": ["get", "go", "day", "work", "going", "quot", "lol", "got", "like", "today"]}

# let's take a look at the data
train_df.head(10)
```

输出结果：

![](https://pic2.zhimg.com/v2-d02b4dca12de2a5d5628cdf6c77685f3_1440w.jpg)

## 2.3 构造dataset生成器

```python
def preprocess(tweet, selected_text, sentiment):
    """
    Will be used in tf.data.Dataset.from_generator(...)

    """
    # The original strings have been converted to
    # byte strings, so we need to decode it
    tweet = tweet.decode('utf-8')
    selected_text = selected_text.decode('utf-8')
    sentiment = sentiment.decode('utf-8')

    tweet = " " + " ".join(str(tweet).split())
    selected_text = " " + " ".join(str(selected_text).split())

    len_st = len(selected_text) - 1
    idx0 = None
    idx1 = None

    for ind in (i for i, e in enumerate(tweet) if e == selected_text[1]):
        if " " + tweet[ind: ind+len_st] == selected_text:
            idx0 = ind
            idx1 = ind + len_st - 1
            break

    char_targets = [0] * len(tweet)
    if idx0 != None and idx1 != None:
        for ct in range(idx0, idx1 + 1):
            char_targets[ct] = 1

    # tokenize with offsets
    enc = TOKENIZER.encode(tweet)
    input_ids_orig = enc.ids
    offsets = enc.offsets

    target_idx = []
    for j, (offset1, offset2) in enumerate(offsets):
        if sum(char_targets[offset1: offset2]) > 0:
            target_idx.append(j)

    target_start = target_idx[0]
    target_end = target_idx[-1]

    # add sentiment word frequency
    sentiment_frequency = []
    pos_fre = 0
    neg_fre = 0
    neu_fre = 0
    for token in enc.tokens:
        token = token.replace("Ġ", "")
        if token in sentiment_dict["positive"]:
            pos_fre += 1
        if token in sentiment_dict["negative"]:
            neg_fre += 1
        if token in sentiment_dict["neutral"]:
            neu_fre += 1
    sentiment_frequency.append(str(pos_fre))
    sentiment_frequency.append(str(neg_fre))
    sentiment_frequency.append(str(neu_fre))
    enc_sentiment = TOKENIZER.encode(" ".join(sentiment_frequency))


    # add and pad data (hardcoded for BERT)
    # --> [CLS] sentiment [SEP] input_ids [SEP] [PAD]
    sentiment_map = {
        'positive': 1313,
        'negative': 2430,
        'neutral': 7974
    }

    input_ids = [0] + input_ids_orig + [2] + [2] + [sentiment_map[sentiment]] + enc_sentiment.ids + [2]
    input_type_ids = [0] * 1 + [0] * (len(input_ids_orig) + 7)
    attention_mask = [1] * (len(input_ids_orig) + 8)
    offsets = [(0, 0)] + offsets + [(0, 0)] * 7
    target_start += pos_offsets
    target_end += pos_offsets

    padding_length = MAX_SEQUENCE_LENGTH - len(input_ids)
    if padding_length > 0:
        input_ids = input_ids + ([1] * padding_length)
        attention_mask = attention_mask + ([0] * padding_length)
        input_type_ids = input_type_ids + ([0] * padding_length)
        offsets = offsets + ([(0, 0)] * padding_length)
    elif padding_length < 0:
        input_ids = input_ids[:padding_length - 1] + [2]
        attention_mask = attention_mask[:padding_length - 1] + [1]
        input_type_ids = input_type_ids[:padding_length - 1] + [0]
        offsets = offsets[:padding_length - 1] + [(0, 0)]
        if target_start >= MAX_SEQUENCE_LENGTH:
            target_start = MAX_SEQUENCE_LENGTH - 1
        if target_end >= MAX_SEQUENCE_LENGTH:
            target_end = MAX_SEQUENCE_LENGTH - 1

    return (
        input_ids, attention_mask, input_type_ids, offsets,
        target_start, target_end, tweet, selected_text, sentiment,
    )

class TweetSentimentDataset(tf.data.Dataset):

    OUTPUT_TYPES = (
        tf.dtypes.int32,  tf.dtypes.int32,   tf.dtypes.int32, 
        tf.dtypes.int32,  tf.dtypes.float32, tf.dtypes.float32,
        tf.dtypes.string, tf.dtypes.string,  tf.dtypes.string,
    )

    OUTPUT_SHAPES = (
        (MAX_SEQUENCE_LENGTH,),   (MAX_SEQUENCE_LENGTH,), (MAX_SEQUENCE_LENGTH,), 
        (MAX_SEQUENCE_LENGTH, 2), (),                     (),
        (),                       (),                     (),
    )

    # AutoGraph will automatically convert Python code to
    # Tensorflow graph code. You could also wrap 'preprocess' 
    # in tf.py_function(..) for arbitrary python code
    def _generator(tweet, selected_text, sentiment):
        for tw, st, se in zip(tweet, selected_text, sentiment):
            yield preprocess(tw, st, se)

    # This dataset object will return a generator
    def __new__(cls, tweet, selected_text, sentiment):
        return tf.data.Dataset.from_generator(
            cls._generator,
            output_types=cls.OUTPUT_TYPES,
            output_shapes=cls.OUTPUT_SHAPES,
            args=(tweet, selected_text, sentiment)
        )

    @staticmethod
    def create(dataframe, batch_size, shuffle_buffer_size=-1):
        dataset = TweetSentimentDataset(
            dataframe.text.values, 
            dataframe.selected_text.values, 
            dataframe.sentiment.values
        )

        dataset = dataset.cache()
        if shuffle_buffer_size != -1:
            dataset = dataset.shuffle(shuffle_buffer_size)
        dataset = dataset.batch(batch_size)
        dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)

        # d = next(iter(dataset))
        # print("Writing example in %d" % (len(dataframe)))
        # for i in range(5):
        #     print("*** Example ***")
        #     print("tokens: %s" % " ".join(TOKENIZER.encode(d[6].numpy()[i].decode("utf-8")).tokens))
        #     print("input_ids: %s" % " ".join([str(x) for x in d[0].numpy()[i]]))
        #     print("input_mask: %s" % " ".join([str(x) for x in d[1].numpy()[i]]))
        #     print("segment_ids: %s" % " ".join([str(x) for x in d[2].numpy()[i]]))
        #     print("selected_text: %s" % d[7].numpy()[i].decode("utf-8"))
        #     print("idx_start: %d" % d[4].numpy()[i])
        #     print("idx_end: %d" % d[5].numpy()[i])

        return dataset

def generate_fold_data(data, num_folds):
    kfold = model_selection.StratifiedKFold(n_splits=num_folds, shuffle=True, random_state=42)
    for fold_num, (train_idx, valid_idx) in enumerate(kfold.split(X=data.text, y=data.sentiment.values)):
        if fold_num == 0:
            save_data = data.iloc[valid_idx]
            save_data["kfold"] = fold_num
        else:
            _save_data = data.iloc[valid_idx]
            _save_data["kfold"] = fold_num
            save_data = pd.concat([save_data, _save_data], axis=0)

    save_data = save_data.reset_index(drop=True)

    return save_data
```

在dataset生成器中，主要生成如下的数据：

- **input_ids** ：每一个词语在词典中的id数字
- **attention_mask** ：标记句子的哪些词语可以mask操作
- **input_type_ids** ：区分前一个句子和后一个句子
- **offsets** ：标记分词之后，每个词语的偏移
- **target_start** ： selected_text中的开头位置
- **target_end** ：selected_text中的结束位置
- **tweet** ：原始的句子
- **selected_text** ： 还有情感的句子
- **sentiment** ：该句子的情感极性

## 2.4 Roberta模型

```python
class RoBertQAModel(TFRobertaPreTrainedModel):
# class RoBertQAModel(TFBertPreTrainedModel):

    DROPOUT_RATE = 0.1
    NUM_HIDDEN_STATES = 2

    def __init__(self, config, *inputs, **kwargs):
        super().__init__(config, *inputs, **kwargs)

        self.bert = TFRobertaModel.from_pretrained(PATH +'pretrained-roberta-base.h5',config=config)
        # self.bert = TFRobertaMainLayer(config, name="bert")
        self.concat = L.Concatenate()
        self.dropout = L.Dropout(self.DROPOUT_RATE)
        self.qa_outputs = L.Dense(
            config.num_labels, 
            kernel_initializer=TruncatedNormal(stddev=config.initializer_range),
            dtype='float32',
            name="qa_outputs")
        self.conv1d_128 = L.Conv1D(128, 2, padding='same')
        self.conv1d_64 = L.Conv1D(64, 2, padding='same')
        self.leakyreLU = L.LeakyReLU()
        self.dense = L.Dense(1, dtype='float32')
        self.flatten = L.Flatten()

        self.dropout_2 = L.Dropout(self.DROPOUT_RATE)
        self.conv1d_128_2 = L.Conv1D(128, 2, padding='same')
        self.conv1d_64_2 = L.Conv1D(64, 2, padding='same')
        self.leakyreLU_2 = L.LeakyReLU()
        self.dense_2 = L.Dense(1, dtype='float32')
        self.flatten_2 = L.Flatten()


    @tf.function
    def call(self, inputs, **kwargs):
        # outputs: Tuple[sequence, pooled, hidden_states]
        x, _, hidden_states = self.bert(inputs, **kwargs)

        # hidden_states = self.concat([
        #     hidden_states[-i] for i in range(1, self.NUM_HIDDEN_STATES+1)
        # ])
        # 
        # hidden_states = self.dropout(hidden_states, training=kwargs.get("training", False))
        # logits = self.qa_outputs(hidden_states)
        # start_logits, end_logits = tf.split(logits, 2, axis=-1)
        # start_logits = tf.squeeze(start_logits, axis=-1)
        # end_logits = tf.squeeze(end_logits, axis=-1)
        return hidden_states[-1], hidden_states[-2]

    @tf.function
    def call_run(self, layer_1, layer_2, is_training):
        x1 = self.dropout(layer_1, training=is_training)
        x1 = self.conv1d_128(x1)
        x1 = self.leakyreLU(x1)
        x1 = self.conv1d_64(x1)
        x1 = self.dense(x1)
        start_logits = self.flatten(x1)
        start_logits = tf.keras.layers.Activation('softmax')(start_logits)

        x2 = self.dropout_2(layer_2, training=is_training)
        x2 = self.conv1d_128_2(x2)
        x2 = self.leakyreLU_2(x2)
        x2 = self.conv1d_64_2(x2)
        x2 = self.dense_2(x2)
        end_logits = self.flatten_2(x2)
        end_logits = tf.keras.layers.Activation('softmax')(end_logits)

        return start_logits, end_logits

    @tf.function
    def adversarial(self, x1, x2, y_true, loss_fn):
        """
        Adversarial training
        """
        # with tf.GradientTape() as tape_perturb:
        #     tape_perturb.watch([x1, x2])
        #     y_pred = self.call_run(x1, x2, is_training=True)
        #     loss1  = loss_fn(y_true[0], y_pred[0])
        #     loss2  = loss_fn(y_true[1], y_pred[1])
        #     
        # perturb1, perturb2 = tape_perturb.gradient([loss1, loss2], [x1, x2])

        y_pred = self.call_run(x1, x2, is_training=True)
        loss1  = loss_fn(y_true[0], y_pred[0])
        loss2  = loss_fn(y_true[1], y_pred[1])
        perturb1 = tf.gradients(loss1, x1, aggregation_method=tf.AggregationMethod.EXPERIMENTAL_ACCUMULATE_N)[0]
        perturb2 = tf.gradients(loss2, x2, aggregation_method=tf.AggregationMethod.EXPERIMENTAL_ACCUMULATE_N)[0]

        #reciprocal in l2 normal
        # perturb_rec = 1 / tf.math.sqrt(tf.reduce_sum(tf.math.pow(perturb1, 2)))
        # perturb1 = 10 * perturb1 * perturb_rec
        perturb1 = 0.02 * tf.math.l2_normalize(tf.stop_gradient(perturb1), axis=-1)
        x1 = x1 + perturb1        

        # perturb_rec = 1 / tf.math.sqrt(tf.reduce_sum(tf.math.pow(perturb2, 2)))
        # perturb2 = 10 * perturb2 * perturb_rec
        perturb2 = 0.02 * tf.math.l2_normalize(tf.stop_gradient(perturb2), axis=-1)
        x2 = x2 + perturb2

        # adv_loss
        y_pred = self.call_run(x1, x2, is_training=True)
        adv_loss  = loss_fn(y_true[0], y_pred[0]) + loss_fn(y_true[1], y_pred[1])
        return adv_loss

    @tf.function
    def virtual_adversarial(self, x1, x2, power_iterations=1, p_mult=0.02):
        bernoulli = tfp.distributions.Bernoulli
        y_pred = self.call_run(x1, x2, is_training=True)
        prob1 = tf.clip_by_value(y_pred[0], 1e-7, 1.-1e-7)
        prob_dist1 = bernoulli(probs=prob1)        
        prob2 = tf.clip_by_value(y_pred[1], 1e-7, 1.-1e-7)
        prob_dist2 = bernoulli(probs=prob2)

        # generate virtual adversarial perturbation
        d1 = tf.keras.backend.random_uniform(shape=tf.shape(x1), dtype=tf.dtypes.float32)
        d2 = tf.keras.backend.random_uniform(shape=tf.shape(x2), dtype=tf.dtypes.float32)
        for _ in range(power_iterations):
            d1 = (0.02) * tf.math.l2_normalize(d1, axis=1)
            d2 = (0.02) * tf.math.l2_normalize(d2, axis=1)
            y_pred = self.call_run(x1 + d1, x2 + d2, is_training=True)
            p_prob1 = tf.clip_by_value(y_pred[0], 1e-7, 1.-1e-7)
            p_prob2 = tf.clip_by_value(y_pred[1], 1e-7, 1.-1e-7)
            kl1 = tfp.distributions.kl_divergence(prob_dist1, bernoulli(probs=p_prob1), allow_nan_stats=False)
            kl2 = tfp.distributions.kl_divergence(prob_dist2, bernoulli(probs=p_prob2), allow_nan_stats=False)

            gradient1 = tf.gradients(kl1, [d1], aggregation_method=tf.AggregationMethod.EXPERIMENTAL_ACCUMULATE_N)[0]
            gradient2 = tf.gradients(kl2, [d2], aggregation_method=tf.AggregationMethod.EXPERIMENTAL_ACCUMULATE_N)[0]
            d1 = tf.stop_gradient(gradient1)
            d2 = tf.stop_gradient(gradient2)
        d1 = p_mult * tf.math.l2_normalize(d1, axis=1)
        d2 = p_mult * tf.math.l2_normalize(d2, axis=1)
        tf.stop_gradient(prob1)
        tf.stop_gradient(prob2)

        #virtual adversarial loss
        y_pred = self.call_run(x1 + d1, x2 + d2, is_training=True)
        p_prob1 = tf.clip_by_value(y_pred[0], 1e-7, 1.-1e-7)
        p_prob2 = tf.clip_by_value(y_pred[1], 1e-7, 1.-1e-7)
        v_adv_loss1 = tfp.distributions.kl_divergence(prob_dist1, bernoulli(probs=p_prob1), allow_nan_stats=False)
        v_adv_loss2 = tfp.distributions.kl_divergence(prob_dist2, bernoulli(probs=p_prob2), allow_nan_stats=False)
        return tf.reduce_mean(v_adv_loss1) + tf.reduce_mean(v_adv_loss2)
```

主要模型的实现，该模型中利用了倒数两层的roberta模型来进行预测，分别预测开头位置和结束位置。同时在之后接上了cov1D操作。最后实现了adversarial training和virtual adversarial training，具体参考论文《 [Adversarial Training Methods for Semi-Supervised Text Classification](https://arxiv.org/abs/1605.07725) 》

## 2.5 Warmup和学习率指数下降

```python
@tf.function
def learning_rate_decay(init_lr, num_train_steps, num_warmup_steps, current_step):
    # Implements linear decay of the learning rate.
    learning_rate = tf.keras.optimizers.schedules.PolynomialDecay(
                    init_lr, num_train_steps, end_learning_rate=0.0, power=1.0)(current_step)

    if num_warmup_steps:
        global_steps_int = tf.cast(current_step, tf.dtypes.int32)
        warmup_steps_int = tf.constant(num_warmup_steps, dtype=tf.dtypes.int32)

        global_steps_float = tf.cast(global_steps_int, tf.dtypes.float32)
        warmup_steps_float = tf.cast(warmup_steps_int, tf.dtypes.float32)

        warmup_percent_done = global_steps_float / warmup_steps_float
        warmup_learning_rate = init_lr * warmup_percent_done

        if global_steps_int < warmup_steps_int:
            learning_rate = warmup_learning_rate
        else:
            learning_rate = learning_rate

    return learning_rate
```

- num_warmup_steps：控制warmup的步数。

## 2.6 focal loss

```python
def focal_loss(y_actual, y_pred, label_smoothing=0.15):
    # label smoothing
    y_actual = tf.cast(y_actual, tf.dtypes.int32)
    y_actual_one_hot = tf.one_hot(y_actual, MAX_SEQUENCE_LENGTH, axis=-1)
    # y_actual_one_hot = y_actual_one_hot * (1 - label_smoothing) + label_smoothing / MAX_SEQUENCE_LENGTH

    # focal loss
    result_reduce = tf.reduce_sum(y_actual_one_hot * y_pred, axis=-1)
    custom_loss = - tf.math.pow((1 - result_reduce), 1) * tf.math.log(result_reduce)
    custom_loss = tf.reduce_mean(custom_loss)
    return custom_loss
```

如果考虑数据集中的不平衡分类问题

## 2.7 joblib并行化训练

```python
num_folds = 5
num_epochs = 5
batch_size = 64
learning_rate = 4e-5
num_train_steps = int(len(train_df) / batch_size * num_epochs)
num_warmup_steps = int(num_train_steps * 0.1)
pos_offsets = 1

data_df_5folds = generate_fold_data(train_df, 5)

def run(fold):
    df_train_fold = data_df_5folds[data_df_5folds.kfold != fold].reset_index(drop=True)
    df_valid_fold = data_df_5folds[data_df_5folds.kfold == fold].reset_index(drop=True)

    num_train_batches = len(df_train_fold) // batch_size + int(len(df_train_fold) % batch_size != 0)
    num_eval_batches = len(df_valid_fold) // batch_size + int(len(df_valid_fold) % batch_size != 0)
    num_test_batches = len(test_df) // batch_size + int(len(test_df) % batch_size != 0)

    # initialize test predictions
    test_preds_start = np.zeros((len(test_df), MAX_SEQUENCE_LENGTH), dtype=np.float32)
    test_preds_end = np.zeros((len(test_df), MAX_SEQUENCE_LENGTH), dtype=np.float32)

    optimizer = tf.keras.optimizers.Adam(learning_rate)
    # optimizer = tf.keras.mixed_precision.experimental.LossScaleOptimizer(
    #     optimizer, 'dynamic')

    # config = RobertaConfig(output_hidden_states=True, num_labels=2)
    config = RobertaConfig.from_json_file(os.path.join(PATH, "config-roberta-base.json"))
    config.output_hidden_states = True
    config.num_labels = 2
    model = RoBertQAModel(config=config)
    # model = RoBertQAModel.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    # config = BertConfig(output_hidden_states=True, num_labels=2)
    # RoBertQAModel.DROPOUT_RATE = 0.2
    # RoBertQAModel.NUM_HIDDEN_STATES = 2
    # model = RoBertQAModel.from_pretrained(PATH, config=config)

    # loss_fn = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=False)
    loss_fn = focal_loss

    loss_step = []
    global_step = tf.Variable(0, name="global_step")
    train_dataset = TweetSentimentDataset.create(
        df_train_fold, batch_size, shuffle_buffer_size=2048)
    valid_dataset = TweetSentimentDataset.create(
        df_valid_fold, batch_size, shuffle_buffer_size=-1)
    test_dataset = TweetSentimentDataset.create(
        test_df, batch_size, shuffle_buffer_size=-1)

    best_score = float('-inf')
    for epoch_num in range(num_epochs):
        # train for an epoch
        train(model, train_dataset, loss_fn, optimizer, global_step, loss_step, num_train_batches, fold)

        # predict validation set and compute jaccardian distances
        pred_start, pred_end, text, selected_text, sentiment, offset = \
            predict(model, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)

        selected_text_pred = decode_prediction(
            pred_start, pred_end, text, offset, sentiment, is_testing=False)
        jaccards = []
        for i in range(len(selected_text)):
            jaccards.append(
                jaccard(selected_text[i], selected_text_pred[i]))

        score = np.mean(jaccards)

        if epoch_num + 1 == num_epochs:
            plt.plot(list(range(global_step.numpy())), loss_step)
            plt.show()
        print("fold = %d , epoch = %d , jaccard = %f" % (fold, epoch_num+1, score))

        if score > best_score:
            best_score = score
            # requires you to have 'fold-{fold_num}' folder in PATH:
            # model.save_pretrained(PATH+f'fold-{fold_num}')
            # or
            # model.save_weights(PATH + f'fold-{fold_num}.h5')

            # predict test set
            test_pred_start, test_pred_end, test_text, _, test_sentiment, test_offset = \
                predict(model, test_dataset, loss_fn, optimizer, num_test_batches, fold)

    # add epoch's best test preds to test preds arrays
    test_preds_start += test_pred_start
    test_preds_end += test_pred_end

    # reset model, as well as session and graph (to avoid OOM issues?) 
    session = tf.compat.v1.get_default_session()
    graph = tf.compat.v1.get_default_graph()
    del session, graph, model
    model = RoBertQAModel(config=config)
    return (test_preds_start, test_preds_end, test_text, test_sentiment, test_offset)

test_result = Parallel(n_jobs=1, backend="threading", verbose=10)(delayed(run)(i) for i in range(num_folds))
```

- n_jobs:控制一次可以有多少个子线程。
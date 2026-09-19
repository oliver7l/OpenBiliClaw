# 使用BERT的两层encoder实现tweet sentiment extraction

- 链接: https://zhuanlan.zhihu.com/p/134599876
- 发布: 2020-04-22 16:55:35
- 赞同: 4 | 评论: 8

---

## 使用BERT的两层encoder实现tweet sentiment extraction

Tweet sentiment extraction是kaggle的一个比赛，这个代码主要是想尝试利用BERT模型实现词语抽取。 其比赛链接： [https://www.kaggle.com/c/tweet-sentiment-extraction/](https://www.kaggle.com/c/tweet-sentiment-extraction)

我在上一篇文章中初步实现了一个以bert为基础的模型，其文章为： [BERT in tweet_sentiment_extraction](https://zhuanlan.zhihu.com/p/132400462) ，发现这个实现效果不怎么好，于是便想着要进一步改进模型。

这篇文章的具体代码实现在： [https://github.com/llq20133100095/tweet_sentiment_extraction/tree/two_layer_classification](https://github.com/llq20133100095/tweet_sentiment_extraction/tree/two_layer_classification)

**比赛背景：** 在日常的微博传播背后，其情绪会影响公司或者个人的决策。捕捉情绪语言能够立刻让人们了解到语言中的情感，从而可以有效 指导决策。但是,哪些词实际上主导情绪描述，这就需要我们模型能够有效挖掘出来。

比如给定一个句子："My ridiculous dog is amazing." [sentiment: positive]。这个句子的情感为positive(积极)，则比赛需要我们抽取出 能够充分表达这个积极情感信息的词语，比如句子中的“amazing”这个词语可以表达positive情感。

## 1.前言

## 1.1 Required

```text
bert-tensorflow
1.15 > tensorflow > 1.12 
tensorflow-hub
```

## 1.2 分析给定的数据

比赛中给定了两个数据集：train.csv和test.csv。利用train.csv数据来构造模型，并预测test.csv数据。

train.csv的具体数据结构如下：

![](https://pic2.zhimg.com/v2-d4319712fcbea043f730e3fdc62a1695_1440w.jpg)

- textID: 文本id
- text： 原始文本
- selected_text： 抽取出来的，带有情感的文本
- sentiment：句子的情感

## 2. 模型构造

## 2.1 数据清洗

模型输入：是把“text”和“sentiment”进行拼接，构造成"[CLS] text [SEP] sentiment [SEP]"。

目前发现在数据集上，selected_text中没有进行数据清洗，里面有很多缺失的词语。通常在开头和结尾处，词语显示不完整。比如：

```text
text: happy birthday
selected_text: y birthday
```

上面在开头缺少了“happy”这个词语，所以需要补上。

同时也存在两个单词没有空格开，比如

```text
text: birthday,say say
selected_text: say say
```

具体清洗代码可以看： [process_data.py](https://github.com/llq20133100095/tweet_sentiment_extraction/blob/two_layer_classification/process_data.py)

## 2.2 模型结构

前一篇文章中，是直接预测每个单词是否需要抽取，这就需要同时构造多个分类器。观望了一下原始数据集，发现抽取到的文本是连续的文本，那么就可以直接标记 **起始位置(start_label)** 和 **结尾位置(end_label)** ，作为预测label 这时候原始的 **N个分类器** 可以缩减到 **2个分类器** 。

本身BERT训练的时候，encoder上共有12层layer。 **实验中使用了最后的一层layer预测start_label,使用倒数第二层预测end_label，这样就可以构造两个分类器来进行预测。**

- 模型如下所示：

![](https://pic1.zhimg.com/v2-616b75ad351cc7d47ddc06a7fd409e5e_1440w.jpg)

其中a为text，b为sentiment。

- 具体代码实现在 [train.py](https://github.com/llq20133100095/tweet_sentiment_extraction/blob/two_layer_classification/train.py) ：

```python
def create_model(bert_config, is_training, is_predicting, input_ids, input_mask, segment_ids,
                 target_start_idx, target_end_idx, num_labels, use_one_hot_embeddings):
    """Creates a classification model."""
    model = modeling.BertModel(
        config=bert_config,
        is_training=is_training,
        input_ids=input_ids,
        input_mask=input_mask,
        token_type_ids=segment_ids,
        use_one_hot_embeddings=use_one_hot_embeddings)

    # Use "pooled_output" for classification tasks on an entire sentence.
    # Use "sequence_output" for token-level output.
    # "get_all_encoder_layers" for all encoder layer
    all_layer = model.get_all_encoder_layers()  # output_layer: 12 layer * [N, max_len, 768]

    hidden_size = all_layer[-1].shape[-1].value
    max_len = all_layer[-1].shape[1].value

    # Create our own layer to tune for politeness data. shape:[N, max_length, num_labels]
    with tf.variable_scope("first_softmax_llq", reuse=tf.AUTO_REUSE):
        output_weights = tf.get_variable("output_weights", [num_labels, 2 * hidden_size],
                                         initializer=tf.truncated_normal_initializer(stddev=0.02))

        output_bias = tf.get_variable("output_bias", [num_labels], initializer=tf.zeros_initializer())

    with tf.variable_scope("loss"):
        output_layer = tf.concat([all_layer[-1], all_layer[-2]], axis=-1)

        # Dropout helps prevent overfitting
        output_layer = tf.layers.dropout(output_layer, rate=0.1, training=is_training)

        # softmax operation
        logits = tf.einsum("nlh,hm->nlm", output_layer, tf.transpose(output_weights))
        logits = tf.nn.bias_add(logits, output_bias)
        # logits_probs = tf.nn.log_softmax(logits, axis=-1)
        start_logits_probs, end_logits_probs = tf.split(logits, 2, axis=-1)
        start_logits_probs = tf.squeeze(start_logits_probs, axis=-1)
        end_logits_probs = tf.squeeze(end_logits_probs, axis=-1)

        # Convert labels into one-hot encoding
        one_hot_start_idx = tf.one_hot(target_start_idx, depth=max_len, dtype=tf.float32)
        one_hot_end_idx = tf.one_hot(target_end_idx, depth=max_len, dtype=tf.float32)

        one_hot_start_labels = tf.one_hot(tf.argmax(start_logits_probs, axis=-1), depth=max_len, dtype=tf.int32, axis=-1)
        one_hot_end_labels = tf.one_hot(tf.argmax(end_logits_probs, axis=-1), depth=max_len, dtype=tf.int32, axis=-1)
        predicted_labels = one_hot_start_labels + one_hot_end_labels

        # If we're predicting, we want predicted labels and the probabiltiies.
        if is_predicting:
          return (predicted_labels, logits)

        # If we're train/eval, compute loss between predicted and actual label
        loss = tf.keras.backend.sparse_categorical_crossentropy(target_start_idx, start_logits_probs, from_logits=True)
        loss += tf.keras.backend.sparse_categorical_crossentropy(target_end_idx, end_logits_probs, from_logits=True)
        loss = tf.reduce_mean(loss)
        return (loss, predicted_labels, logits)
```
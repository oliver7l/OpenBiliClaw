# Relation Classification in TAC40

- 链接: https://zhuanlan.zhihu.com/p/158780496
- 发布: 2020-07-11 20:00:05
- 赞同: 0 | 评论: 0

---

## 1.背景

用roberta模型，来进行关系分类，主要用到的数据集为TAC40关系分类数据集。

直接使用transformers导入roberta模型。

同时使用TPU分布式计算，加快模型训练速度。

具体的代码地址为： [relation_classification_kbp](https://zhuanlan.zhihu.com/relation_classification_kbp.ipynb)

## 2.Requirement

- transformers
- tensorflow 2.0
- numpy
- sklearn

## 3.代码分析

### 3.1 TPU设置

要使用TPU来训练，首先要进行设置，导入tensorflow的tpu设置如下：

```python
# Detect hardware, return appropriate distribution strategy
try:
    # TPU detection. No parameters necessary if TPU_NAME environment variable is
    # set: this is always the case on Kaggle.
    tpu = tf.distribute.cluster_resolver.TPUClusterResolver()
    print('Running on TPU ', tpu.master())
except ValueError:
    tpu = None

if tpu:
    tf.config.experimental_connect_to_cluster(tpu)
    tf.tpu.experimental.initialize_tpu_system(tpu)
    strategy = tf.distribute.experimental.TPUStrategy(tpu)
else:
    # Default distribution strategy in Tensorflow. Works on CPU and single GPU.
    strategy = tf.distribute.get_strategy()

print("REPLICAS: ", strategy.num_replicas_in_sync)
```

### 3.2 分词和编码

要使用roberta，则需要利用transformers的分词器来对原始文本进行分词和编码，得到三种不同的输入： - input_ids: 把每个词语转换成词典中的id - attention_mask: 标记哪些词语可以进行mask操作 - token_type_ids: 区分前后句子id

```python
# First load the real tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL)

def regular_encode(texts, tokenizer, maxlen=512):
    enc_di = tokenizer.batch_encode_plus(
        texts, 
        return_attention_masks=True, 
        return_token_type_ids=True,
        pad_to_max_length=True,
        max_length=maxlen
    )

    return np.array(enc_di['input_ids'], dtype=np.int32), np.array(enc_di['attention_mask'], dtype=np.int32), np.array(enc_di["token_type_ids"], dtype=np.int32)
```

### 3.3 建立模型

这里主要用到了两中pooling操作，分别是maxpooling和averagepooling，然后进行拼接，最后输出结果。

```python
def build_model(transformer, max_len=512):
    """
    https://www.kaggle.com/xhlulu/jigsaw-tpu-distilbert-with-huggingface-and-keras
    """
    input_word_ids = Input(shape=(max_len,), dtype=tf.int32, name="input_word_ids")
    input_mask = Input(shape=(max_len,), dtype=tf.int32, name="input_mask")
    segment_ids = Input(shape=(max_len,), dtype=tf.int32, name="segment_ids")
    sequence_output = transformer((input_word_ids, input_mask, segment_ids))[0]
    gp = tf.keras.layers.GlobalMaxPooling1D()(sequence_output)
    ap = tf.keras.layers.GlobalAveragePooling1D()(sequence_output)
    stack = tf.keras.layers.concatenate([gp, ap], axis=1)
    # stack = tf.keras.layers.Dropout(0.2)(stack)
    out = Dense(40, activation='softmax')(stack)


    model = Model(inputs=[input_word_ids, input_mask, segment_ids], outputs=out)
    model.compile(Adam(lr=1e-6), loss=tf.keras.losses.SparseCategoricalCrossentropy(), metrics=['accuracy'])

    return model
```

### 3.4 构建输入数据集

利用dataset，构建输入数据集

```python
train_dataset = (
    tf.data.Dataset
    .from_tensor_slices((x_train, train_labels))
    .repeat()
    .shuffle(50)
    .batch(BATCH_SIZE)
    .prefetch(AUTO)
)

test_dataset = (
    tf.data.Dataset
    .from_tensor_slices((x_test, test_labels))
    .batch(BATCH_SIZE)
    .cache()
    .prefetch(AUTO)
)
```

### 3.5 开始训练模型

训练模型，并进行模型评估

```python
n_steps = len(train_texts) // BATCH_SIZE
train_history = model.fit(
    train_dataset,
    steps_per_epoch=n_steps,
    validation_data=test_dataset,
    epochs=70,
    shuffle=True,
)

test_predicts = model.predict(x_test)
result = F1_scores(test_labels, test_predicts)
print(result)
```

## 4.Results

最后的F1值结果显示：

![](https://pica.zhimg.com/v2-447fea2c556ccb10e2d5999940f7d25a_1440w.png)

F1值能够达到：77.6%
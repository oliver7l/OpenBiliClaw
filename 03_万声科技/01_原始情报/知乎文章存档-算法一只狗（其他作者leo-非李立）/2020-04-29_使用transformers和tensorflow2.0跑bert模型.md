# 使用transformers和tensorflow2.0跑bert模型

- 链接: https://zhuanlan.zhihu.com/p/137222913
- 发布: 2020-04-29 15:57:12
- 赞同: 18 | 评论: 18

---

**1.前言**

前面两篇文章其实已经详细介绍了bert在kaggle比赛tweet_sentiment_extraction的作用，但是该比赛是基于tensorflow2.0版本的，因此需要把代码进行转换。前面的两篇文章如下链接：

[https://zhuanlan.zhihu.com/p/132400462](https://zhuanlan.zhihu.com/p/132400462)

[https://zhuanlan.zhihu.com/p/134599876](https://zhuanlan.zhihu.com/p/134599876)

## 2. 使用tensorflow2.0 版跑 bert模型和roberta模型

在kaggle中使用notebook参加比赛，是基于tensorflow2.0版本的，虽然不太想换版本跑，但是为了能够用它上面的GPU 还是借鉴了kaggle论坛上面的代码。其中要特别感谢大佬：

- [@Abhishek Thakur](https://www.kaggle.com/abhishek) ，其代码启发了我使用并行化加速训练模型，能够以更短的时间内训练5-fold模型。

要在tensorflow2.0上跑bert和roberta模型，需要安装 [transformers](https://huggingface.co/transformers/installation.html) :

```text
pip install transformers
```

### 2.1 加载transformers中的分词包

因为要构建bert模型的输入，因此加载词典，同时把输入句子转换成下面三个部分： - input_ids: 把每个token转换为对应的id - attention_mask： 记录哪些词语需要mask - input_type_ids： 区分两个句子，前一句子标记为0，后一句子标记为1

例如：

```text
*** Example ***
tokens: happy b ##day !
input_ids: 101 3407 1038 10259 999 102 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
input_mask: 1 1 1 1 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
segment_ids: 1 1 1 1 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
selected_text: happy bday!
```

导入分词器工具：

```python
# set some global variables
PATH = "../input/huggingfacetransformermodels/model_classes/roberta/roberta-large-tf2-model/"
MAX_SEQUENCE_LENGTH = 128
TOKENIZER = BertWordPieceTokenizer(f"../input/my-data/vocab.txt", lowercase=True, add_special_tokens=False)
```

其中 `TOKENIZER` 可以直接实现分词，转换id等操作：

```python
enc = TOKENIZER.encode(tweet)
input_ids_orig, offsets = enc.ids, enc.offsets
```

### 2.2 自定义bert模型层

这里需要输出bert的全部12层，然后取最后两层作为输出。最后一层预测开始的位置start_logits，倒数第二层预测结束为止end_logits

```python
class BertQAModel(TFBertPreTrainedModel):

    DROPOUT_RATE = 0.1
    NUM_HIDDEN_STATES = 2

    def __init__(self, config, *inputs, **kwargs):
        super().__init__(config, *inputs, **kwargs)

        self.bert = TFBertMainLayer(config, name="bert")
        self.concat = L.Concatenate()
        self.dropout = L.Dropout(self.DROPOUT_RATE)
        self.qa_outputs = L.Dense(
            config.num_labels, 
            kernel_initializer=TruncatedNormal(stddev=config.initializer_range),
            dtype='float32',
            name="qa_outputs")

    @tf.function
    def call(self, inputs, **kwargs):
        # outputs: Tuple[sequence, pooled, hidden_states]
        _, _, hidden_states = self.bert(inputs, **kwargs)

        hidden_states = self.concat([
            hidden_states[-i] for i in range(1, self.NUM_HIDDEN_STATES+1)
        ])

        hidden_states = self.dropout(hidden_states, training=kwargs.get("training", False))
        logits = self.qa_outputs(hidden_states)
        start_logits, end_logits = tf.split(logits, 2, axis=-1)
        start_logits = tf.squeeze(start_logits, axis=-1)
        end_logits = tf.squeeze(end_logits, axis=-1)

        return start_logits, end_logits
```

### 2.3 预加载模型

利用transformers，可以快速实现预加载模型，同时transformers这个库中已经集成了多种模型.

在加载模型之前，需要导入模型的基本设置：

```python
config = RobertaConfig.from_json_file(os.path.join(PATH, "config.json"))
config.output_hidden_states = True
config.num_labels = 2
```

接下来加载模型：

```python
model = RoBertQAModel.from_pretrained(PATH, config=config)
```

### 2.4 并行化处理（使用多线程）

本来训练一次模型需要1100s，如果训练两个模型，则需要1100*2s的时间。使用多线程后，在训练两个模型的时候，可以把时间缩短到 1100s左右。

通过使用joblib包，来实现多线程，从而压缩训练时间，它的使用方法也很简单，仅仅只需要几行代码就可以实现：

```python
from joblib import Parallel, delayed
test_result = Parallel(n_jobs=num_folds, backend="threading")(delayed(run)(i) for i in range(num_folds))
```

- run()函数是我们自己实现的函数，里面主要实现了模型的训练和预测过程
- n_jobs：用来定义共有多少个线程可以实现。

实验结果可以看出，5-fold可以缩短1000s:

![](https://pic2.zhimg.com/v2-3f6e859820b78c7807b92ec07d99412f_1440w.jpg)
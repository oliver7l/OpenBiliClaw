# kaggle：Tweet Sentiment Extraction，提升分数的几种主要方法

- 链接: https://zhuanlan.zhihu.com/p/258898869
- 发布: 2020-09-24 17:21:30
- 赞同: 1 | 评论: 0

---

## ﻿1.背景

这次分享的是kaggle比赛Tweet Sentiment Extraction的第13名次的方法，作者主要用针对数据集进行前置处理或者是后置处理，具体原文： [https://www.kaggle.com/c/tweet-sentiment-extraction/discussion/159505](https://www.kaggle.com/c/tweet-sentiment-extraction/discussion/159505)

## 2.主要方法

## 2.1 Roberta Model

这次比赛，大部分都是用了Roberta模型，其基础的模型使用demo如下： [https://www.kaggle.com/cdeotte/tensorflow-roberta-0-705](https://www.kaggle.com/cdeotte/tensorflow-roberta-0-705)

这个方法仅仅在离线的时候取得0.705分数（CV），在公共排行榜上取得0.709，私人排行榜上取得0.713。这些分数值都比较低，作者利用了以下几种方法，来提升模型效果。

## 2.2 不需要在文本上移除额外的空格

![](https://pic2.zhimg.com/v2-511806450363738c873e8b2118a9e57b_1440w.png)

可以观察到原始的句子，如果原始句子前有空格，则抽取出来的文本包含了额外的字符 `"s"` 。因此在处理原有文本的时候，不需要移除开头处的额外空格。

## 2.2 把特殊符号进行分割

RoBERTa模型会把符号 `...` 当作一个整体，但句子 `This is fun...` 抽取出来的 `fun.` 不包括多个 `.` ，因此需要把 `...` 转换为 `.` , `.` , `.` 。类似的符号 `..` , `!!` , `!!!` 也是这样处理。

## 2.3 不要轻信Train数据集

这个比赛用的是Jaccard score指标，这个指标主要是匹配两个字符串之间的单词是否完全一直。如果训练集中存在句子 `" Matt loves ice cream"` 和其选择的文本"t love"。用抽取的文本 `"love"` 来训练模型，而不是使用 `"Matt love"` 作为目标句子。

## 2.4 修改模型结构

首先输出end index，然后利用end index的输出与RoBerta最后一层进行拼接，用来预测start index。

```python
# ROBERTA
bert_model = TFRobertaModel.from_pretrained('roberta-base')
x = bert_model(q_id,attention_mask=q_mask,token_type_ids=q_type)

# END INDEX HEAD
x2 = tf.keras.layers.Dropout(0.1)(x[0]) 
x2b = tf.keras.layers.Dense(1)(x2)
x2 = tf.keras.layers.Flatten()(x2b)
x2 = tf.keras.layers.Activation('softmax')(x2)

# START INDEX HEAD
x1 = tf.keras.layers.Concatenate()([x2b,x[0]])
x1 = tf.keras.layers.Dropout(0.1)(x1) 
x1 = tf.keras.layers.Dense(1)(x1)
x1 = tf.keras.layers.Flatten()(x1)
x1 = tf.keras.layers.Activation('softmax')(x1)

# MODEL
model = tf.keras.models.Model(inputs=[q_id, q_mask, q_type], outputs=[x1,x2])
```

## 2.5 使用label smoothing

```python
loss = tf.keras.losses.CategoricalCrossentropy(label_smoothing=0.2)
```

## 2.6 Mask words

掩码部分单词。掩码概率为0.05，利用 `50264` 作为掩码：

```python
r = np.random.uniform(0,1,ids.shape)
ids[r<0.05] = 50264 
ids[tru] = self.ids[indexes][tru]
```

其中 `tru` 用来保存特殊字符，保证特殊字符不会被取代。

## 2.7 Decay learning rate

衰退学习率

```python
def lrfn(epoch):
    dd = {0:4e-5,1:2e-5,2:1e-5,3:5e-6,4:2.5e-6}
    return dd[epoch]
lr = tf.keras.callbacks.LearningRateScheduler(lrfn, verbose=True)
```

## 2.8 利用100%的train data来预测测试集

在使用5-fold和提早停止之后，我们就可以知道有多少个迭代的效果时最好的。此时有两种方法可以进行测试集预测：

- 在每个fold中，预测测试集，把5次得到的结果进行求和平均，用来作为最后的预测
- 在最后预测测试集时，直接用全部的train data来进行预测：

```python
t_gen = DataGenerator(ids,att,tok,tru,tar1,tar2)
model.fit(t_gen, epochs=3, verbose=1, callbacks=[lr],
        validation_data=([ids[idxV,],att[idxV,],tok[idxV,]], [tar1[idxV,],tar2[idxV,]]))
```

## 2.9 类别不平衡

在tensorflow比较容易进行类别不平衡操作，把 `positive` 和 `negative` 的权重设置为2， `neutral` 设置为1。

## 2.10 Post process（后置处理）

使用了上面的9种方法，还是会在预测的时候出现过多的噪声。比如：

- `" that's awesome!!!"` ，目标提取的文本为 `"s awesome!"`
- `" I'm thinking... wonderful."` ，目标提取的文本为 `". wonderful"`

模型会看到两个空格，同时会提取一个字符出来。

然而模型不能够打破一个单词，比如例子 `"went fishing and loved it"` （目标文本 `"d loved"` ），这就需要直接把 `and` 这个单词进行拆分。因此需要做后处理。这种方法能够在CV上提升0.0025，在LB上提升0.0025

```python
# INPUT s=predicted, t=text, ex=sentiment
# OUTPUT predicted with PP

def applyPP(s,t,ex):

    t1 = t.lower()
    t2 = s.lower()

    # CLEAN PREDICTED
    b = 0
    if len(t2)>=1:
        if t2[0]==' ': 
            b = 1
            t2 = t2[1:]
    x = t1.find(t2)

    # PREDICTED MUST BE SUBSET OF TEXT
    if x==-1:
        print('CANT FIND',k,x)
        print(t1)
        print(t2)
        return s

    # ADJUST FOR EXTRA WHITE SPACE
    p = np.sum( np.array(t1[:x].split(' '))=='' )
    if (p>2): 
        d = 0; f = 0
        if p>3: 
            d=p-3
        return t1[x-1-b-d:x+len(t2)]

    # CLEAN BAD PREDICTIONS
    if (len(t2)<=2)|(ex=='neutral'):
        return t1

    return s
```
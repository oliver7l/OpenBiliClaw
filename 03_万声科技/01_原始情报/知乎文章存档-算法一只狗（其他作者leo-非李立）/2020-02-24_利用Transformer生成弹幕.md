# 利用Transformer生成弹幕

- 链接: https://zhuanlan.zhihu.com/p/108818902
- 发布: 2020-02-24 15:10:42
- 赞同: 4 | 评论: 0

---

## 1.前言

最近研究了Transformer模型，主要参考了github里面对Transformer的实现，其代码为： [https://github.com/Kyubyong/transformer](https://github.com/Kyubyong/transformer)

而我自己实现的弹幕生成代码的github地址为： [https://github.com/llq20133100095/transformer_barrages](https://github.com/llq20133100095/transformer_barrages)

## 2.模型原理

## 2.1 Transformer模型

Transformer模型已经有很多大佬介绍了，这里我只是简单贴出常见的模型图：

![](https://pic4.zhimg.com/v2-6d913cb59ca85e7a992154b32d89d9b3_1440w.jpg)

这里如果要使用transformer生成弹幕，则需要有效构建数据集。

## 2.2 数据集构建

这里用到了弹幕的数据集，同时利用sentencepiece对句子进行分词。分词之后的句子如下所示：

```text
▁仙女 有盒子
▁怎么不开车 去捡 啊
```

sentencepiece分词有一个好处就是，词语前面有“▁”代表的是独立的词语，而没有这个前缀的，则代表的是可以进行前后组合的词语，比如“去捡”+“啊”，这两个词语可以组成一个词语。

接下来则构建输入和输出句子的形式。

- encode中输入句子：句子 + </s>
- decode中输入句子：<s> + 句子
- decode中输出句子：<s> + 句子 + </s>

具体例子如下：

```text
原始句子：▁仙女 有盒子
encode中输入句子：▁仙女 有盒子 </s>
decode中输入句子：<s> ▁仙女 有盒子
decode中输出句子：<s> ▁仙女 有盒子 </s>
```

## 2.3 弹幕生成时的操作

最后训练好模型时，在测试的时候不断拼接生成的新向量输入到encode和decode中，形成循环生成，代码中的写法如下：

```python
for _ in tqdm(range(3)):
    memory, sents1, src_masks = self.encode(xs, False)

    logits, y_hat, y, sents2 = self.decode(ys, memory, src_masks, False)
    if tf.reduce_sum(y_hat, 1) == self.token2idx["<pad>"]: break

    # concat input
    _x = tf.concat((x, random_id(logits)), 1)
    xs = (_x, seqlens, sents1)

    _decoder_inputs = tf.concat((decoder_inputs, random_id(logits)), 1)
    ys = (_decoder_inputs, y, y_seqlen, sents2)
```

其中重新构造了 `_x` 进行输入，同时也重新构造 `_decoder_inputs` 输入。

## 3.执行过程

## 3.1 Train

- STEP 1. 运行下面的命令，生成预处理的弹幕语料

```text
python pretreatment/prepro.py
```

如果你想调整默认的词典大小(default:32000)，可以进行下面的命令：

```text
python pretreatment/prepro.py --vocab_size 8000
```

它会创建两个文件 `barrages_data/prepro` and `barrages_data/segmented` .

- STEP 2. 训练模型

```text
python train.py
```

参数设置放在 `hparams.py` ，可以根据里面的参数进行对应设置，比如：

```text
python train.py --logdir myLog --batch_size 256 --dropout_rate 0.5
```

- STEP 3. 根据输入的句子，生成弹幕

```text
python barrrages_generate.py
```

## 3.2 Result

当输入：

```text
老司机
```

输出句子：

![](https://pic4.zhimg.com/v2-d622da1fcc2751ab9f2282aea53e2f53_1440w.jpg)

## 3.3 运行在微信界面上

运行代码:

```text
python ichat_robot.py
```

结果：

![](https://pic3.zhimg.com/v2-00215f97459eb3555827194788019cb2_1440w.jpg)
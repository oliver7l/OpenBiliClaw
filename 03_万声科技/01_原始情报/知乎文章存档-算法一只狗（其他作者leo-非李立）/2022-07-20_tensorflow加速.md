# tensorflow加速

- 链接: https://zhuanlan.zhihu.com/p/544166967
- 发布: 2022-07-20 21:40:14
- 赞同: 1 | 评论: 0

---

## 1. 构造TFRecord文件

TFrecord文件是tensorflow用来快速读取大文件的一种二进制格式，使用这种格式可以较为轻松的迁移到不同的系统上进行快速训练。

具体流程：

- 构造TFRecord文件： `tf.io.TFRecordWriter(record_file)`
- 读取TFRecord文件： `tf.data.TFRecordDataset`

### 1.2 构造TFRecord文件

- TFRecord文件中多维度矩阵的存储，因此需要利用 `_bytes_feature` 进行二进制存储，同时 `_float_feature` 和 `_int64_feature` 只能保存二维矩阵
- 利用 `tf.train.Example(features=tf.train.Features(feature=feature))` 函数定义对应的特征字段，最后使用 `tf.io.TFRecordWriter` 进行保存

```python
writer = tf.io.TFRecordWriter(record_file)

class UseTfRecord:
    # The following functions can be used to convert a value to a type compatible
    # with tf.Example.
    def _bytes_feature(self, value):
        """Returns a bytes_list from a string / byte."""
        if isinstance(value, type(tf.constant(0))):
            value = value.numpy() # BytesList won't unpack a string from an EagerTensor.
        return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))

    def _float_feature(self, value):
        """Returns a float_list from a float / double."""
        return tf.train.Feature(float_list=tf.train.FloatList(value=[value]))

    def _int64_feature(self, value):
        """Returns an int64_list from a bool / enum / int / uint."""
        return tf.train.Feature(int64_list=tf.train.Int64List(value=[value]))

    def _parse_image_function(self, example_proto):
        image_feature_description = {
            'save_vol_attack_time': tf.io.FixedLenFeature([], tf.string),
            'macro_seq_features': tf.io.FixedLenFeature([], tf.string),
            'micro_seq_features': tf.io.FixedLenFeature([], tf.string),
            'target_seq_features': tf.io.FixedLenFeature([], tf.string),
            'tactics_seq_features': tf.io.FixedLenFeature([], tf.string),
            'seq_statics_feature': tf.io.FixedLenFeature([], tf.string),
            'duel_id': tf.io.FixedLenFeature([], tf.int64),
            'tar_tactics': tf.io.FixedLenFeature([], tf.string),
            'all_label': tf.io.FixedLenFeature([], tf.int64),
            "position_map_features": tf.io.FixedLenFeature([], tf.string),
            "dis_me_all": tf.io.FixedLenFeature([], tf.string),
            "team_tactics_seq_time": tf.io.FixedLenFeature([], tf.string),
            "defogging": tf.io.FixedLenFeature([], tf.string),
            "fogging": tf.io.FixedLenFeature([], tf.string),
        }

        # Parse the input tf.Example proto using the dictionary above.
        return tf.io.parse_example(example_proto, image_feature_description)

    def image_example(self, save_vol_attack_time, macro_seq_features, micro_seq_features, target_seq_features, tactics_seq_features, seq_statics_feature, \
                        position_map_features, dis_me_all, team_tactics_seq_time, \
                        duel_id, tar_tactics, defogging, fogging, all_label):
        """
        Create a dictionary with features that may be relevant.
        """
        feature = {
            'save_vol_attack_time': self._bytes_feature(save_vol_attack_time),
            'macro_seq_features': self._bytes_feature(macro_seq_features),
            'micro_seq_features': self._bytes_feature(micro_seq_features),
            'target_seq_features': self._bytes_feature(target_seq_features),
            'tactics_seq_features': self._bytes_feature(tactics_seq_features),
            'seq_statics_feature': self._bytes_feature(seq_statics_feature),
            'tar_tactics': self._bytes_feature(tar_tactics),
            "position_map_features": self._bytes_feature(position_map_features),
            "dis_me_all": self._bytes_feature(dis_me_all),
            "team_tactics_seq_time": self._bytes_feature(team_tactics_seq_time),
            'all_label': self._int64_feature(all_label),
            'duel_id': self._int64_feature(duel_id),
            'defogging': self._bytes_feature(defogging),
            'fogging': self._bytes_feature(fogging),
        }
        return tf.train.Example(features=tf.train.Features(feature=feature))

    def save_tfrecord(self, writer, save_vol_attack_time, macro_seq_features, micro_seq_features, target_seq_features, tactics_seq_features, seq_statics_feature, \
                        position_map_features, dis_me_all, team_tactics_seq_time, \
                        duel_id, tar_tactics, defogging, fogging, all_label):
        for _save_vol_attack_time, \
            _macro_seq_features, _micro_seq_features, \
            _target_seq_features, _tactics_seq_features, \
            _seq_statics_feature, _position_map_features, _dis_me_all, _team_tactics_seq_time, \
            _duel_id, _tar_tactics, _defogging, _fogging, _all_label \
                in zip(save_vol_attack_time, \
                        macro_seq_features, micro_seq_features, \
                        target_seq_features, tactics_seq_features, \
                        seq_statics_feature, position_map_features, dis_me_all, team_tactics_seq_time, \
                        duel_id, tar_tactics, defogging, fogging, all_label):

            tf_example = self.image_example(_save_vol_attack_time.tostring(), \
                                                _macro_seq_features.tostring(), _micro_seq_features.tostring(), \
                                                _target_seq_features.tostring(), _tactics_seq_features.tostring(), \
                                                _seq_statics_feature.tostring(), _position_map_features.tostring(), _dis_me_all.tostring(), _team_tactics_seq_time.tostring(), \
                                                _duel_id, _tar_tactics.tostring(), _defogging.tostring(), _fogging.tostring(), _all_label)
            writer.write(tf_example.SerializeToString())
```

### 1.3 读取TFRecord文件

- 利用 `tf.data.TFRecordDatase` 读取上面构造出来的TFRecord文件

```python
raw_dataset = tf.data.TFRecordDataset(record_file)

use_tfrecord = UseTfRecord()
parsed_dataset = raw_dataset.map(use_tfrecord._parse_image_function)

parsed_dataset = parsed_dataset.batch(self.hp.batch_size).prefetch(tf.data.experimental.AUTOTUNE)
```

## 2.Dataset加速：提升GPU利用率

### 2.1 GPU内存占用率问题

GPU占用率问题往往是因为模型大小和Batch size大小引起的。如果发现GPU占用率比较少，比如在30%~50%，这时候可以改变batch size的大小，尽量利用完整个GPU的内存。batch size设置为128，与设置为256相比，内存占用率是接近于2倍关系。当你batch  size设置为128，占用率为40%的话，设置为256时，此时模型的占用率约等于80%。

### 2.2 GPU利用率问题

![](https://picx.zhimg.com/v2-060060ea082b33ee136014df018e14af_1440w.jpg)

这个是Volatile GPU-Util表示，当没有设置好CPU的线程数时，这个参数是在反复的跳动的，0%，20%，70%，95%，0%。这样停息1-2 秒然后又重复起来。其实是GPU在等待数据从CPU传输过来，当从总线传输到GPU之后，GPU逐渐起计算来，利用率会突然升高，但是GPU的算力很强大，0.5秒就基本能处理完数据，所以利用率接下来又会降下去，等待下一个batch的传入。因此，这个GPU利用率瓶颈在内存带宽和内存介质上以及CPU的性能上面。

**这时候可以用过设置Dataset里的线程数和预读数据来提升GPU的利用率。**

> Prefetching

预取与训练步骤的预处理和模型执行重叠。当模型执行训练步骤s时，输入管道正在读取步骤s+1的数据。这样做可以将步长时间减少到训练和提取数据所需时间的最大值。

```python
parsed_dataset = parsed_dataset.batch(self.hp.batch_size).prefetch(tf.data.experimental.AUTOTUNE)
```

> Map

设置多线程读取数据。在读取TFRecord文件时，可以并行读取样本数据，关键点在设计 `num_parallel_calls` 线程数

```python
parsed_dataset = raw_dataset.map(use_tfrecord._parse_image_function,     
                                 num_parallel_calls=tf.data.experimental.AUTOTUNE)
```

## 3.容器IO

在容器中，如果把数据保存在CFS盘上，会产生额外的IO时间，因此需要把数据迁移到容器的本地磁盘上进行读取。

## 4.加速效果

- CPU模型效果：

用了30核cpu跑模型，cpu占用率位3281%：

![](https://pic4.zhimg.com/v2-a7efc0d973ace377eb7d2af29bd084c9_1440w.png)

在850次训练迭代中，共20w样本，耗费时间在 **241s：**

![](https://pica.zhimg.com/v2-67622b2969747caafa9a9b058b25bb04_1440w.jpg)

- GPU加速效果

GPU采用了batch size为 `512` ，CPU占用率仅为187.5%：

![](https://picx.zhimg.com/v2-bdbf12943da3de99a52d1723cfdd1579_1440w.png)
*image.png*

在相同样本量下只需要 **196s：**

![](https://picx.zhimg.com/v2-6c2facf6b8debc4d59d84f20f33dc88f_1440w.jpg)
*image.png*
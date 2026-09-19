# ﻿pandas中使用tqdm

- 链接: https://zhuanlan.zhihu.com/p/245781247
- 发布: 2020-09-15 18:01:18
- 赞同: 1 | 评论: 0

---

一般使用python的tqdm，可以查看到处理数据的进度，但是如果想要把tqdm应用到pandas中，则需要改变一下用法。

同样需要先导入tqdm模块：

```python
from tqdm import tqdm
```

使用tqdm.pandas，同时input_data为Dataframe结构，因此需要使用input_data.progress_apply来实现apply操作：

```python
def get_feature(input_data):
    """
    Get feature data
    """
    feature_data = np.zeros([len(input_data), hp.feature_len])
    error_sample_index = []

    tqdm.pandas(desc="get feature in input_data")
    input_data.progress_apply(lambda x: apply_feature(x, feature_data, error_sample_index), axis=1)

    if len(error_sample_index) != 0:
        feature_data = np.delete(feature_data, error_sample_index, axis=0)
    return feature_data
```

最后的结果显示如下：

![](https://pic1.zhimg.com/v2-dde4b3dda5958c879c3ee44f18e332ee_1440w.png)

详细的代码如下（可以略过）：

```python3
from sklearn.datasets.samples_generator import make_blobs
from sklearn.cluster import DBSCAN
from itertools import cycle
from sklearn.preprocessing import StandardScaler
from sklearn.manifold import TSNE
from tqdm import tqdm
from time import time
from util import Hparams
from read_data import read_input_data

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

hparams = Hparams()
parser = hparams.parser
hp = parser.parse_args(args=[])

def t_sne(data_zs, pre_labels):
    """
    1. Use T-SNE in feature reduction.
    2. plot
    """
    pre_labels_dataframe = pd.DataFrame(pre_labels, columns=["labels"])

    tsne = TSNE()
    tsne.fit_transform(data_zs)
    tsne_data = pd.DataFrame(tsne.embedding_)
    
    for label_id in list(set(pre_labels)):
        d = tsne_data[pre_labels_dataframe["labels"] == label_id]
        plt.plot(d[0], d[1], '.', label=label_id)
    
    plt.legend(bbox_to_anchor=(1, 0), loc=3, borderaxespad=0)
    plt.savefig("./analysis/dbscan_tsne.png")
    # plt.show()

def apply_feature(d, feature_data, error_sample_index):
    """
    Process each line_data. Transform the char to ID
    """
    def _char_to_id(char_str):
        return [int(ord(x)-33) for x in char_str] + [ord("-")-33]

    def _supply_list(d, column):
        """
        supply the list to 64
        """
        other_info_list = _char_to_id(d[column])
        list_len = len(other_info_list)
        if list_len < 64:
            for _ in range(64 - list_len):
                other_info_list.append(0)
        return other_info_list
    
    manifest_raw = _supply_list(d, "other_info1_str") \
                   + _supply_list(d, "other_info2_str") \
                   + [int(d["other_info3_int"])]
    
    if len(manifest_raw) == hp.feature_len:
        feature_data[d["index"]] += np.array(manifest_raw)
    else:
        error_sample_index.append(d["index"])

def normalize_max_min(input_data):
    """
    normalization: max_min method
    """
    max_value = np.max(input_data, axis=0)
    min_value = np.min(input_data, axis=0)

    input_data = (input_data- min_value) / ((max_value - min_value) + 0.0001)
    return input_data                      

def get_feature(input_data):
    """
    Get feature data
    """
    feature_data = np.zeros([len(input_data), hp.feature_len])
    error_sample_index = []

    tqdm.pandas(desc="get feature in input_data")
    input_data.progress_apply(lambda x: apply_feature(x, feature_data, error_sample_index), axis=1)

    if len(error_sample_index) != 0:
        feature_data = np.delete(feature_data, error_sample_index, axis=0)
    return feature_data

def use_dbscan(feature_data):
    start_time = time()
    db = DBSCAN(eps=0.3, min_samples=10)
    db.fit(feature_data)
    print("Finish DBSCAN: %f" % (time() - start_time))

    # core sample indices
    core_samples_mask = np.zeros_like(db.labels_, dtype=bool)
    core_samples_mask[db.core_sample_indices_] = True

    # labels 
    pre_labels = db.labels_

if __name__ == "__main__":
    """
    1. read data
    """
    input_data_select = read_input_data(hp.input_file)

    """
    2. get feature
    """
    feature_data = get_feature(input_data_select)
    feature_data = normalize_max_min(feature_data)
```
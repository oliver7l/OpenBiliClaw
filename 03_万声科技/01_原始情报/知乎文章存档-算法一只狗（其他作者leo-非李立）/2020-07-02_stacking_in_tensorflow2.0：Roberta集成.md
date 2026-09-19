# stacking in tensorflow2.0：Roberta集成

- 链接: https://zhuanlan.zhihu.com/p/153525261
- 发布: 2020-07-02 22:28:07
- 赞同: 0 | 评论: 0

---

## 1.前言

使用stacking方法，提升tweet sentiment的抽取效果。其stacking代码如下： [https://github.com/llq20133100095/tweet_sentiment_extraction/blob/other_mission2/thinking/ensamble/roberta-adversarial-dropout_0.715_en.ipynb](https://github.com/llq20133100095/tweet_sentiment_extraction/blob/other_mission2/thinking/ensamble/roberta-adversarial-dropout_0.715_en.ipynb)

背景是kaggle的比赛： **tweet_semtiment_extraction**

## 2.方法

## 2.1 stacking

首先简单介绍一下stacking的方法，它是一种比较常见的集成学习方法。由多层结果所组成。在具体的代码中，我主要使用了两层的结构：

- **第一层结构** ：用了6个结果相似的模型进行搭建，6个模型的输出进行求和平均，然后把结构输入到第二层结构中；
- **第二层结构** ：直接使用了softmax进行分类，得到预测结果。

![](https://pic1.zhimg.com/v2-50527198c35754e3bc34a860d38fe9a0_1440w.jpg)

在实验中，首先是把training data进行5-fold，把原有的training data分为5份，其中1份作为 **vaild data** ，剩余的4份则作为训练集训练每一个模型。进行了5-fold之后，得到5分vaild data的新特征，这种新特征就可以作为第二层的模型特征输入。

在第二层的模型中，不需要用到太复杂的模型，用一些简单的模型就可以了。

## 2.2 具体代码

- 运行第一层的6个模型，得到融合特征：

```python
def run_first_stack(fold, dataframe_stack_train1, dataframe_stack_train2):
    df_train_fold = data_df_5folds[data_df_5folds.kfold != fold].reset_index(drop=True)
    df_valid_fold = data_df_5folds[data_df_5folds.kfold == fold].reset_index(drop=True)

    num_train_batches = len(df_train_fold) // batch_size + int(len(df_train_fold) % batch_size != 0)
    num_eval_batches = len(df_valid_fold) // batch_size + int(len(df_valid_fold) % batch_size != 0)
    num_test_batches = len(test_df) // batch_size + int(len(test_df) % batch_size != 0)

    optimizer = tf.keras.optimizers.Adam(learning_rate)
    # optimizer = tf.keras.mixed_precision.experimental.LossScaleOptimizer(
    #     optimizer, 'dynamic')

    config = RobertaConfig.from_json_file(os.path.join(PATH, "config-roberta-base.json"))
    config.output_hidden_states = True
    config.num_labels = 2
    model1 = RoBertQAModel1.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model1.load_weights(f'../input/roberta-dropout02-adversarial/fold-{fold}.h5')

    model2 = RoBertQAModel2.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model2.load_weights(f'../input/tf-roberta-base/fold-{fold}.h5')

    model3 = RoBertQAModel3.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model3.load_weights(f'../input/tf-adversarial-training2/fold-{fold}.h5')

    model4 = RoBertQAModel4.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model4.load_weights(f'../input/tf-roberta-base-768/fold-{fold}.h5')

    model5 = RoBertQAModel5.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model5.load_weights(f'../input/tf-roberta-base-three/fold-{fold}.h5')

    model6 = RoBertQAModel1.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    model6.load_weights(f'../input/tf-roberta-base-715/fold-{fold}.h5')

    # model7 = RoBertQAModel2.from_pretrained(os.path.join(PATH, "pretrained-roberta-base.h5"), config=config)
    # model7.load_weights(f'../input/tf-roberta-base-01/fold-{fold}.h5')

    loss_fn = focal_loss

    loss_step = []
    global_step = tf.Variable(0, name="global_step")
    # train_dataset = TweetSentimentDataset.create(
    #     df_train_fold, batch_size, shuffle_buffer_size=2048)
    valid_dataset = TweetSentimentDataset.create(
        df_valid_fold, batch_size, shuffle_buffer_size=-1)
    test_dataset = TweetSentimentDataset.create(
        test_df, batch_size, shuffle_buffer_size=-1)

    pred_start1, pred_end1, text, selected_text, sentiment, offset, vaild_target_start, vaild_target_end = \
        predict1(model1, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    pred_start2, pred_end2, _, _, _, _ = \
        predict2(model2, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    pred_start3, pred_end3, _, _, _, _, _, _ = \
        predict1(model3, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    pred_start4, pred_end4, _, _, _, _ = \
        predict2(model4, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    pred_start5, pred_end5, _, _, _, _ = \
        predict2(model5, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    pred_start6, pred_end6, _, _, _, _, _, _ = \
        predict1(model6, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)
    # pred_start7, pred_end7, _, _, _, _ = \
    #     predict2(model7, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)

    pred_start1 += pred_start2 + pred_start3 + pred_start4 + pred_start5 + pred_start6
    pred_end1 += pred_end2 + pred_end3 + pred_end4 + pred_end5 + pred_end6
    pred_start1 = pred_start1 / 6
    pred_end1 = pred_end1 / 6

    if dataframe_stack_train1 is None:
        dataframe_stack_train1 = dataframe_stack_generate(pred_start1, pred_end1, text, selected_text, sentiment, offset, vaild_target_start, vaild_target_end, fold)
        dataframe_stack_train2 = dataframe_stack_generate(pred_start2, pred_end2, text, selected_text, sentiment, offset, vaild_target_start, vaild_target_end, fold)
    else:
        _dataframe_stack_train1 = dataframe_stack_generate(pred_start1, pred_end1, text, selected_text, sentiment, offset, vaild_target_start, vaild_target_end, fold)
        _dataframe_stack_train2 = dataframe_stack_generate(pred_start2, pred_end2, text, selected_text, sentiment, offset, vaild_target_start, vaild_target_end, fold)
        dataframe_stack_train1 = pd.concat([dataframe_stack_train1, _dataframe_stack_train1], axis=0)
        dataframe_stack_train2 = pd.concat([dataframe_stack_train2, _dataframe_stack_train2], axis=0)

    test_pred_start1, test_pred_end1, test_text, _, test_sentiment, test_offset, test_target_start, test_target_end = \
            predict1(model1, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    test_pred_start2, test_pred_end2, _, _, _, _ = \
            predict2(model2, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    test_pred_start3, test_pred_end3, _, _, _, _, _, _ = \
            predict1(model3, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    test_pred_start4, test_pred_end4, _, _, _, _ = \
            predict2(model4, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    test_pred_start5, test_pred_end5, _, _, _, _ = \
            predict2(model5, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    test_pred_start6, test_pred_end6, _, _, _, _, _, _ = \
            predict1(model6, test_dataset, loss_fn, optimizer, num_test_batches, fold)
    # test_pred_start7, test_pred_end7, _, _, _, _ = \
    #         predict2(model7, test_dataset, loss_fn, optimizer, num_test_batches, fold)

    test_pred_start1 += test_pred_start2 + test_pred_start3 + test_pred_start4 + test_pred_start5 + test_pred_start6
    test_pred_end1 += test_pred_end2 + test_pred_end3 + test_pred_end4 + test_pred_end5 + test_pred_end6
    test_pred_start1 = test_pred_start1 / 6
    test_pred_end1 = test_pred_end1 / 6

    return dataframe_stack_train1, dataframe_stack_train2, test_pred_start1, test_pred_end1, test_text, test_sentiment, test_offset, test_target_start, test_target_end
```

上述代码中，得到了平均的验证集输出特征，同时也得到了测试集的平均值输出特征。 其中没有进行重新训练，而是把保存好的模型进行load_weights。

- 第二层模型：

```python
def run_second_stack(test_preds_start, test_preds_end, test_text, test_sentiment, test_offset, test_target_start, test_target_end):

    dataframe_stack_test = dataframe_stack_generate(test_preds_start, test_preds_end, test_text, test_text, test_sentiment, test_offset, test_target_start, test_target_end, 0)

    # initialize second test predictions
    test_preds_start = np.zeros((len(test_df), MAX_SEQUENCE_LENGTH), dtype=np.float32)
    test_preds_end = np.zeros((len(test_df), MAX_SEQUENCE_LENGTH), dtype=np.float32)

    # second train
    for fold in range(num_folds):
        dataframe_stack_data1_train = dataframe_stack_train1[dataframe_stack_train1.fold != fold].reset_index(drop=True)
        # dataframe_stack_data2_train = dataframe_stack_train2[dataframe_stack_train2.fold != fold].reset_index(drop=True)
        dataframe_stack_data1_vaild = dataframe_stack_train1[dataframe_stack_train1.fold == fold].reset_index(drop=True)

        num_train_batches = len(dataframe_stack_data1_train) // batch_size + int(len(dataframe_stack_data1_train) % batch_size != 0)
        num_eval_batches = len(dataframe_stack_data1_vaild) // batch_size + int(len(dataframe_stack_data1_vaild) % batch_size != 0)
        num_test_batches = len(dataframe_stack_test) // batch_size + int(len(dataframe_stack_test) % batch_size != 0)

        optimizer = tf.keras.optimizers.Adam(learning_rate)
        loss_fn = focal_loss

        # model 
        model = StackingDnn()

        loss_step = []
        global_step = tf.Variable(0, name="global_step")

        train_dataset1 = StackingDataset.create(
            dataframe_stack_data1_train, batch_size, shuffle_buffer_size=2048)
        # train_dataset2 = StackingDataset.create(
        #     dataframe_stack_data2_train, batch_size, shuffle_buffer_size=2048)
        valid_dataset = StackingDataset.create(
            dataframe_stack_data1_vaild, batch_size, shuffle_buffer_size=-1)
        test_dataset = StackingDataset.create(
            dataframe_stack_test, batch_size, shuffle_buffer_size=-1)

        best_score = float('-inf')
        for epoch_num in range(num_epochs):
            # train for an epoch
            stacking_train(model, train_dataset1, loss_fn, optimizer, global_step, loss_step, num_train_batches, fold)
            # stacking_train(model, train_dataset2, loss_fn, optimizer, global_step, loss_step, num_train_batches, fold)

            # predict validation set and compute jaccardian distances
            pred_start, pred_end, text, selected_text, sentiment, offset = \
                stacking_predict(model, valid_dataset, loss_fn, optimizer, num_eval_batches, fold)

            selected_text_pred = decode_prediction(pred_start, pred_end, text, offset, sentiment, is_testing=False)
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
                model.save_weights(f'fold-{fold}.h5')

                # predict test set
                test_pred_start, test_pred_end, test_text, _, test_sentiment, test_offset = \
                    stacking_predict(model, test_dataset, loss_fn, optimizer, num_test_batches, fold)

        best_score_list.append(best_score)
        # add epoch's best test preds to test preds arrays
        test_preds_start += test_pred_start
        test_preds_end += test_pred_end

        # reset model, as well as session and graph (to avoid OOM issues?) 
        session = tf.compat.v1.get_default_session()
        graph = tf.compat.v1.get_default_graph()
        del session, graph, model
        model = StackingDnn()
    return (test_preds_start, test_preds_end, test_text, test_sentiment, test_offset)
```
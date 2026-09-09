# Rank+to+Rerank序反馈 · 提炼笔记

> 来源：`01_原始资料库/03_工作资料/工作资料_腾讯/2020年09月-内部资料/推荐系统/Rank+to+Rerank序反馈.doc` ｜ 13130字

## 一句话定位
关于通用推荐广告相关的技术资料（《Rank+to+Rerank序反馈》），含 25 条关键要点。

## 核心内容摘要
每一刷请求，推荐系统从排序(rank)到机制(rerank)会依据召回的视频的特征（eg. 品类、热门cp、时效等）做一次重排序，即rerank_score=rank_score*CatTagRescorer*DurationRescorer*NegFeedbackRescorer*... ，经过重排序的视频会经过cp，品类等等去重逻辑做过滤，展示过滤后rerank_score最大的10个视频；视频时长参数

## 结构大纲
- 1.框架图
- 2.序关系critera
- 单个参数对rank的序影响从三个方面衡量：
- 3.机制参数列表
- 目前已有的rerank参数：
- 1.单次请求的数据分析
- 1.1 探索性数据分析
- 1.1.2 召回的视频量：273
- 0.257239
- 1.4809E-46
- 0.263064
- 0.571321
- 0.930069
- 1.15548
- 1.4E-45
- 0.3
- 1.3
- 0.980199
- 0.253082
- 1.24446E-30
- 0.255814
- 0.764595
- 1.15219
- 1.56E-29
- 0.23566

## 关键方法 · 模型 · 指标
- 每一刷请求，推荐系统从排序(rank)到机制(rerank)会依据召回的视频的特征（eg. 品类、热门cp、时效等）做一次重排序，即rerank_score=rank_score*CatTagRescorer*DurationRescorer*NegFeedbackRescorer*... ，经过重排序的视频会经过cp，品类等等去重逻辑做过滤，展示过滤后rerank_score最大的10个视频
- 视频时长参数
- 用户tag显示负反馈参数
- 1.1.2 召回的视频量：273
- import seaborn as sns
- return ranksum,ranksump
- for steps in rerankstep_list:
- rank_to_rerank_dict['pctr']=[]
- rank_to_rerank_dict['pcvr']=[]
- rerankstep=vid_rank_info_list[7]
- filter out 的视频的序变化：Wilcoxon ranksums
- kde_kws={'color': 'k', 'lw': 3, 'label':'KDE'},
- rank_j_top=rank_j.sort_values(ascending=False)[:10]
- plt.xlabel('{} distribution'.format(param), fontsize=15)
- TimeDecayRescorer, HotCpRescorer 对召回视频整体序以及top10的视频序变化影响较大
- rerankstep_list=[x for x in rerankstep.split('&') if x!='']
- rank_j_index = rank_j.sort_values(ascending=False).index.values
- rank_to_rerank_dict['pctr'].append(float(vid_rank_info_list[5]))
- rank_to_rerank_dict['pcvr'].append(float(vid_rank_info_list[6]))
- dftmp=df.sort_values(by='rerank_score', ascending=False).reset_index()
- ranksum,ranksump=stats.ranksums(filter_rank_index,filter_rerank_index)
- 1.1.3 rank_score分布以及rerank_score分布：由图可知rerank_score相比较rank_score更集中分布于0附近
- 由于rank to rerank各特征的rescore的计算逻辑是人为设置的可能会造成rank_score的序与rerank_score的序产生较大差异，因此需要建设一个序反馈机制使得rank to rerank的序变化在可控范围内
- 1.2.1 全序列累积params序变化：kendall tau, pearson correlation coefficient 均表现出当乘入TimeDecayRescorer, HotCpRescorer后整体的序变化较大，且趋近于无序
- 1.1.5 rank_score前10的视频信息以及rerank_score做filter处理后的前10的视频信息对比：由数据可知重排序前后的top10的rank_score和rerank_score差异较大，并且TimeDecayRescorer, HotCpRescorer的数值差异较大

## 与推荐/广告/数据科学面试的关联
- 召回策略（向量/图/协同过滤/多路）
- 排序模型（Wide&Deep/DIN/ESMM/树模型）与重排
- CTR/CVR 预估与校准
- 用户画像/分层/生命周期
- 特征工程/embedding

## 关键术语
CTR、CVR、DIN、KDE、KS、MS、PV

## 与本人项目的关联
通用推荐广告（文中出现相关关键词）

## 价值评估
**高** —— 与本人项目（通用推荐广告）直接关联，且含方法/指标/术语


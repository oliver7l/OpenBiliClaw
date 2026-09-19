# 一个名字引发的血案: left-pad 和 npm 的那些事

- 链接: https://mp.weixin.qq.com/s/VHY5kRbCxJdzbOK9ki9smg
- 发布: 2016-03-28
- 来源: 微信公众号 AlgorithmDog

---

近日，一个只有 11 代码的 left-pad 被作者 unpublished。 npm 圈子因此闹得鸡犬不宁。究竟发生了什么呢？

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNtOrD7GtBXgEJaA2lpTrWhibfq5XxQvpUAqvLmtXnYOMr5Ss3QH8vIiadSRuOzXTeqYy3urG7uFGNg/0?wx_fmt=png)

发生了啥？

      Azer 是一名 Javascript 程序员，他写了 273 个 Javascript 封包放在 npm 上供大家使用。一个叫 left-pad 的封包包，虽然只有 11 行，但被上千个项目用到，其中包括著名的 babel 和 react-native。

module.exports = leftpad;

function leftpad(str,len, ch){

      str = String(str);

      var i = -1;

      ch || (ch = '  ');

      len = len - str.length;

      while(++i < len){ 

         str = ch + str; 

      }

}

      一天 Azer 收到一封邮件。邮件上说，“Hi, Azer. 我们是 kik 公司的，我们要发布我们的封包 kik, 发现 kik 这个名字已经被你占用了。你能把名字改改嘛？”。

      Azer 内心 os "大哥，有木有搞错，这个名字我已经用了"，便回了一封邮件说，“我拒绝”。

      kik公司收到回复之后，又给 Azer 发了一封关于敬酒罚酒的邮件，Azer 再拒绝。。。就这样几个回合之后， kik 公司灵机一动，心想“我干嘛和你死磕，我找 npm”，于是给 npm 公司发了一封关于敬酒罚酒的邮件。

      npm 表示我们做了一个艰难的决定，然后把 kik 封包转给 kik 公司了。

      Azer “吾操汝母！”，一怒之下将自己在 npm 上的 273 个封包全部撤下，其中就包括 left-pad 封包。一石激起千层浪，依赖 left-pad 的上千个项目包括 babel 和 react-native 瞬间崩溃。大量开发者看着自己项目构建失败，顿时被吓尿。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNtOrD7GtBXgEJaA2lpTrWhDmvUZvuv8xT3ia46dvVrKxxfDkoVQDxb3IX82ucBGwOVTsweCezFemw/0?wx_fmt=png)

Javascript 的微型封包

      看到这个事情，我的第一反应: 就 11 行的代码有必要搞个封包嘛？这个封包可真够微型的。不是只有我这么想，好多人吐槽的好吧。比如这篇文章 NPM & left-pad: Have We Forgotten How To Program? 怒斥大家还会不会写代码，疾呼“函数不是封包”。文章还举了一些更加匪夷所思的例子，比如 isArray 被下载了1800W次，被另外 72 个包依赖，实际上这个封包只有一行代码

 return toString.call(arr) == '[object Array]';

除了写文章，另一些人则用行为艺术吐槽。有人建立了一个 left-pad 服务。还有人建立了一个检查输入数字是不是 13 的封包 is-thirteen，然后在 Github 上获得 700 多个赞了。      

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNtOrD7GtBXgEJaA2lpTrWhEzaT9qVJY9VMgcO2tbD18QoIO0PLn0f85Cukz0sl8VibaYnIlciaacUw/0?wx_fmt=png)

      大家对这类微型封包的吐槽是有道理的。微型封包最大的问题在于，你为了完成一个完整项目引入很多微型封包，导致你的项目需要依赖很多东西。多一个依赖就意味着你多了一份不可控性。只要其中一个微型封包代码出了问题，你的项目就坑爹了啊。

      其实，这一类函数是有意义的，其最大的意义在于 hiding complexity。函数开发者写好这些函数并进行充分测试，提供给其他程序员使用。这些程序员就不用重复前人的工作，而是把时间和精力放在更有生产效率的地方。正如有人在 github 讨论中说道，“你怎么知道一个数等于 -0 呢？ x === 0 && 1 / x === -Infinity 确实很简单。但你真的希望你怎么写并搞清楚为什么嘛？还是你更倾向于直接使用 negative-zero呢”。大家吐槽的是为什么一个函数就能形成一个封包。因为 Javascript 提供的标准函数库非常小，只能完成一些基本操作，很多功能都不具备。大家又要用，便各自写各自的。如果只是这样，还不致于一行代码一个封包。npm 和 bower 等包管理出现使得 Javascript 拥有非常方便快捷的包管理，大家都可把自己写的小函数传上去，别人可以很方便地下载这些函数并导入自己的项目中。Javascript 标准函数库不足，加上方便快捷的包管理，程序员们养成一个函数一个封包的习惯就不足为奇了。现在已经有一些人开始解决这个问题。比如 lodash 和 underscore，这两个封包就可以用于来弥补 Javascript 标准库的不足。

命名空间

      由于 npm 将 kik 控制权转移，被各路人马骂惨了。npm 的官方说明 kik left-pad and npm 被骂得关闭了评论。那么 Maven, Github 和 DockerHub 等分发平台是怎么解决这个问题的呢？对比这些分发平台，我们可以发现 npm 的症结在于没有命名空间。Maven、 GitHub 和 DockerHub 封包名是两家命名 Group_id (Userid) /Artifact_id, 而 npm 的封包名直接是Artifact_id。

      没有命名空间会带来很多麻烦。比如，你以为下面的代码是 Google 官方出品的重量级封包？

nmp install google

Too young, too simple, sometimes naive! 虽然它是调用 Google 搜索的封包，但它本身和 Google 官方没有半毛钱关系。用它自己的话说就是 “A module to search and scrape google. This is not sponsored, supported, or affiliated with Google Inc.”。 有一天 Google 想发布自己的 google 的封包的时候，就懵逼啦。稍微能理解 kik 公司了吧？

      再比如，一名开发者开发一个封包，一时不知道取啥名字，随手取了一个名字 ntt。经过一段时间的开发和推广，有人开始在自己的项目中使用 ntt 封包，开发者也因此获得了一定的知名度。这时候，日本电信公司 NTT（Nippon Telegraph and Telephone, NTT）要求开发者将这个封包让出来。你要是这位开发者你能忍？对 Azer 的遭遇是不是更加感同身受了？

      如果 npm 有命名空间，那就没有这个问题了。比如，我要发布了一个 google 封包, 全名叫lietal/google。没有人会觉得这个东东来自 google 吧。 我再发布一个 lietal/ntt， ntt 公司也不至于叽叽哇哇。

一点思考

     软件工程领域著名的《人月神话》用了一些的政治隐喻。比如，它的第 4 章的标题是“贵族专制、民主政治和系统设计”。再比如，它的第 7 章 “为什么巴别塔会失败” 也进行了一些政治性讨论。我以前看的时候，只觉得例子好奇怪，没有什么其他的感觉。现在的 left-pad 和 npm 事件倒让我意识到了现实的复杂性。代码孕育政治啊！

    欢迎大家扫描二维码关注公众号。

![](http://mmbiz.qpic.cn/mmbiz/Q3H1TCddfvNtOrD7GtBXgEJaA2lpTrWhLibpfOGJrorcBZALdJJ0GmRhZAUU0r9sW9lic7Hyyj77wfIJQq6KfOkA/0?wx_fmt=jpeg)

预览时标签不可点

 Read more

Scan to Follow

 Got It

 Scan with Weixin to 
use this Mini Program

 Cancel
 Allow

 Cancel
 Allow

 Cancel
 Allow

 ×
 分析

![](http://mmbiz.qpic.cn/mmbiz_png/AVt8qQ587W9b7ZXDWm3wtwn8N2k5wDkxSuZLLhibf8BiaWG4ibdEjFB2xzLHH8vSuODG7xvVyqan0f7VY3QS6dSkHSzibTtZOzicKASp2Ubeypgs/0?wx_fmt=png)

微信扫一扫可打开此内容，
使用完整服务

 <script
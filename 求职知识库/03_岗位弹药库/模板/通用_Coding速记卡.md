# 通用 · Coding 速记卡（面试前 20 分钟过一遍）

> 情报依据：拼多多/字节等**每轮技术面都有 coding**，且**偏经典题不偏难怪**（力扣 Hot 100 为主）。
> 这份不是题解全集，而是**答题流程 + 六大题型模板 + 真题清单**，目的是"不挂"。

---

## 一、答题五步流程（比刷题更重要）

| 步骤 | 动作 | 时间 |
|---|---|---|
| 1 | **复述题意 + 确认边界**：空输入？数据量级？是否考虑溢出/负数？返回值类型？ | 30s |
| 2 | **说思路 + 复杂度**，确认面试官认可 | 30s |
| 3 | **写代码时开口说**，让面试官跟上你的思路 | — |
| 4 | **写完主动跑一个样例**（别等面试官说） | 30s |
| 5 | **分析时空复杂度**，主动提"如果要优化，瓶颈在 X" | 30s |

> ⚠️ 卡住时的正确做法：**说出卡在哪 + 给个暴力解**，然后问"我可以先写暴力解再优化吗"。
> 面试官看的是思路，不是你是否一次写对。

---

## 二、六大题型模板

### 1. DFS / BFS（图遍历、岛屿、二叉树）
```python
# 网格 DFS 模板（岛屿数量）
def numIslands(grid):
    if not grid: return 0
    m, n = len(grid), len(grid[0])
    def dfs(i, j):
        if i < 0 or i >= m or j < 0 or j >= n or grid[i][j] != '1':
            return
        grid[i][j] = '#'          # 就地标记，省 visited
        for di, dj in ((1,0),(-1,0),(0,1),(0,-1)):
            dfs(i+di, j+dj)
    ans = 0
    for i in range(m):
        for j in range(n):
            if grid[i][j] == '1':
                ans += 1; dfs(i, j)
    return ans
```
**要点**：就地标记省空间；递归深度大改 BFS（用 deque）

### 2. 二叉树（最大路径和、遍历）
```python
def maxPathSum(root):
    ans = float('-inf')
    def dfs(node):            # 返回"从 node 向下走的最大贡献值"
        nonlocal ans
        if not node: return 0
        l = max(dfs(node.left), 0)     # 负贡献就砍掉
        r = max(dfs(node.right), 0)
        ans = max(ans, node.val + l + r)   # 经过 node 的路径
        return node.val + max(l, r)        # 只能选一边继续往上
    dfs(root); return ans
```
**要点**：区分"经过当前节点的路径"和"向上贡献"，这是二叉树路径题的通用套路

### 3. 动态规划（打家劫舍、LIS、回文）
```python
# 打家劫舍（一维 DP + 空间优化）
def rob(nums):
    prev = cur = 0
    for x in nums:
        prev, cur = cur, max(cur, prev + x)
    return cur

# 最长递增子序列 O(n log n)（耐心排序）
from bisect import bisect_left
def lengthOfLIS(nums):
    d = []
    for x in nums:
        i = bisect_left(d, x)
        if i == len(d): d.append(x)
        else: d[i] = x
    return len(d)
```
**要点**：DP 题先定义状态含义，再写转移；一维 DP 常能压到 O(1) 空间

### 4. 滑动窗口（无重复字符、最长子串）
```python
def lengthOfLongestSubstring(s):
    last = {}          # 字符 -> 最后出现位置
    left = ans = 0
    for right, c in enumerate(s):
        if c in last and last[c] >= left:
            left = last[c] + 1
        last[c] = right
        ans = max(ans, right - left + 1)
    return ans
```
**要点**：模板是"右指针扩张 → 不满足则收缩左指针 → 更新答案"

### 5. 单调栈（右侧更大元素、柱状图最大矩形）
```python
# 单调递减栈：找右边第一个更大元素
def nextGreater(nums):
    res = [-1] * len(nums)
    st = []                        # 存下标
    for i, x in enumerate(nums):
        while st and nums[st[-1]] < x:
            res[st.pop()] = x
        st.append(i)
    return res
```
**要点**：栈内存下标而非值；递减栈找"更大"，递增栈找"更小"

### 6. 二分查找（边界模板）
```python
def lower_bound(a, target):      # 第一个 >= target 的位置
    l, r = 0, len(a)             # 注意 r = len(a) 开区间
    while l < r:
        mid = (l + r) // 2
        if a[mid] < target: l = mid + 1
        else: r = mid
    return l
```
**要点**：统一用 `[l, r)` 开区间写法，避免死循环；`lower_bound` / `upper_bound` 只差一个等号

---

## 三、真题清单（按优先级）

### 🔴 P0（必练，各岗位面经反复出现）
| 题 | 考点 | 来源 |
|---|---|---|
| LC 200 岛屿数量 | DFS/BFS/并查集 | 拼多多三面 |
| LC 124 二叉树最大路径和 | 二叉树 DFS | 拼多多二面（追问答 N 叉树扩展） |
| LC 300 最长递增子序列 | DP / 二分 | 拼多多一面 |
| LC 3 无重复字符最长子串 | 滑动窗口 | 大模型二面 |
| LC 5 最长回文子串 | DP / 中心扩展 | 大模型三面 |

### 🟡 P1（高频）
| 题 | 考点 |
|---|---|
| 打家劫舍（含改版） | DP |
| LC 198/213 打家劫舍 I/II | DP 环形变体 |
| 向右看人数 | 单调栈 |
| 区间内 3 的倍数子串 | 前缀和 + 数学 |
| 字符串替换最小字典序 | 贪心 |
| 推箱子模拟 | 模拟 |

### 🟢 P2（SQL / 大数据）
- 用户行为分析 SQL：留存、漏斗、分组 TopN（窗口函数 `row_number()`）
- 去重计数、`case when` 透视、自连接求留存

---

## 四、SQL 速记（数据科学/策略岗常考）

```sql
-- 次日留存
SELECT a.dt,
       COUNT(DISTINCT b.user_id) * 1.0 / COUNT(DISTINCT a.user_id) AS retention
FROM (SELECT DISTINCT dt, user_id FROM t_active) a
LEFT JOIN (SELECT DISTINCT dt, user_id FROM t_active) b
  ON a.user_id = b.user_id AND b.dt = a.dt + 1
GROUP BY a.dt;

-- 分组 TopN（每组取前 3）
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY grp ORDER BY score DESC) rn
  FROM t
) WHERE rn <= 3;
```

---

## 五、最后 5 分钟自检

- [ ] 网格 DFS 会就地标记吗？
- [ ] 二叉树路径题能分清"经过"和"贡献"吗？
- [ ] DP 状态定义能一句话说清吗？
- [ ] 滑动窗口的左右指针边界处理对吗？
- [ ] 二分用 `[l, r)` 统一写法了吗？

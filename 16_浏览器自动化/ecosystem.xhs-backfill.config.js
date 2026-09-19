// PM2 配置：小红书缺正文低密度回填（每 2 小时 :05 触发一次，脚本内随机憩志 0-30min 防风控）
// 启动：pm2 start ecosystem.xhs-backfill.config.js
// 保存：pm2 save  开机自启：pm2 startup
module.exports = {
    apps: [
        {
            name: "xhs-backfill",
            // 用 shell 包装显式 unset PYTHONHOME/PYTHONPATH（空串会让
            // Python init 崩 No module named 'encodings'），见 xhs_backfill.sh
            script: "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/16_浏览器自动化/xhs_backfill.sh",
            interpreter: "/bin/bash",
            cron_restart: "5 */2 * * *",
            autorestart: false,
            max_restarts: 1,
            min_uptime: "30s",
            time: true,
            out_file: "/Users/imac/Library/Logs/xhs-backfill.out.log",
            error_file: "/Users/imac/Library/Logs/xhs-backfill.err.log",
        },
    ],
};
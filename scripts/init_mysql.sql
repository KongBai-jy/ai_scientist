-- AI Scientist 数据库初始化脚本 (MySQL)
-- 请在 Navicat 中打开此文件并执行

-- 1. 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS agent_snapshot_db 
DEFAULT CHARACTER SET utf8mb4 
DEFAULT COLLATE utf8mb4_unicode_ci;

USE agent_snapshot_db;

-- 2. 创建快照表
CREATE TABLE IF NOT EXISTS snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    round VARCHAR(10) NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    question TEXT NOT NULL,
    overall_score FLOAT NOT NULL,
    explorer_output JSON NOT NULL,
    scientist_output JSON NOT NULL,
    critic_output JSON NOT NULL,
    granularity_stats JSON NOT NULL,
    human_feedback JSON NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. 创建反馈表
CREATE TABLE IF NOT EXISTS feedback (
    id INT AUTO_INCREMENT PRIMARY KEY,
    feedback_id VARCHAR(36) NOT NULL UNIQUE,
    round VARCHAR(10) NOT NULL,
    target_agent VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    constraint_type VARCHAR(20) NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. 验证
SELECT '数据库初始化完成!' as Status;
SELECT 'snapshots' as Table_Name, COUNT(*) as Row_Count FROM snapshots
UNION ALL
SELECT 'feedback', COUNT(*) FROM feedback;

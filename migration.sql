-- ============================================================
-- Supabase 数据库重置 + 认证迁移 SQL
-- 注意：此操作会删除所有旧数据！
-- 执行方式：Supabase Dashboard → SQL Editor → 粘贴执行
-- ============================================================

-- ==================== 1. 删除旧表 ====================
DROP TABLE IF EXISTS fishbone_configs;
DROP TABLE IF EXISTS datasets;

-- ==================== 2. 重建 datasets 表 ====================
CREATE TABLE datasets (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    data        JSONB NOT NULL,
    columns_info JSONB,
    row_count   INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_datasets_user_id ON datasets(user_id);

-- ==================== 3. 重建 fishbone_configs 表 ====================
CREATE TABLE fishbone_configs (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    problem     TEXT,
    raw_input   TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_fishbone_configs_user_id ON fishbone_configs(user_id);

-- ==================== 4. 启用 RLS ====================
ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE fishbone_configs ENABLE ROW LEVEL SECURITY;

-- ==================== 5. RLS 策略 — datasets ====================
CREATE POLICY "用户读取自己的数据集"    ON datasets FOR SELECT  USING (auth.uid() = user_id);
CREATE POLICY "用户插入自己的数据集"    ON datasets FOR INSERT  WITH CHECK (auth.uid() = user_id);
CREATE POLICY "用户更新自己的数据集"    ON datasets FOR UPDATE  USING (auth.uid() = user_id);
CREATE POLICY "用户删除自己的数据集"    ON datasets FOR DELETE  USING (auth.uid() = user_id);

-- ==================== 6. RLS 策略 — fishbone_configs ====================
CREATE POLICY "用户读取自己的鱼骨图"    ON fishbone_configs FOR SELECT  USING (auth.uid() = user_id);
CREATE POLICY "用户插入自己的鱼骨图"    ON fishbone_configs FOR INSERT  WITH CHECK (auth.uid() = user_id);
CREATE POLICY "用户更新自己的鱼骨图"    ON fishbone_configs FOR UPDATE  USING (auth.uid() = user_id);
CREATE POLICY "用户删除自己的鱼骨图"    ON fishbone_configs FOR DELETE  USING (auth.uid() = user_id);

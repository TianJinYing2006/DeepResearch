-- 0002_run_artifacts.sql —— L3-A 任务终局产物（P2-B）
--
-- 报告正文 / 导出载荷等「终局产物」的权威副本；P8 可将大正文迁对象存储，
-- 本表退化为产物清单（路径 + 校验和）。口径见需求 10 §5.2 与 §5.4。

CREATE TABLE run_artifacts (
    run_id     text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    kind       text NOT NULL,
    body       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, kind)
);

COMMENT ON TABLE run_artifacts IS '任务终局产物（报告正文 / 导出载荷）；一 run 一种 kind 一行，覆盖式更新';
COMMENT ON COLUMN run_artifacts.kind IS '产物类型：report_md / export_json（新增类型需在需求 10 §5.4 登记）';

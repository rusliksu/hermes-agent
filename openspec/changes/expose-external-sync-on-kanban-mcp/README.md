# expose-external-sync-on-kanban-mcp

PR #15 доставил минимальный forward-port фактической Kanban MCP поверхности и
guarded external sync tool. Одобренный material delta планирует отдельный
dry-run-first helper PR для exact-SHA standalone rollout и rollback без live
действий до следующего явного gate.

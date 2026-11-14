## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
- ALWAYS read graphify output (graphify-out/GRAPH_REPORT.md, then `graphify query`) FIRST, before looking at any other file or source in this project.
- ALWAYS update the graphify output after EVERY code change: run `graphify update .` right after writing or editing code, before moving on.
- ALWAYS follow the documents under docs/ (docs/coding-conventions.md, docs/user-scenarios.md, and any others added there). Act according to them in every task; if a request conflicts with them, point out the conflict before proceeding.

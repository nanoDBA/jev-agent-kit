# 02. Prior art

Pinned commits are in `/sources.lock.json`. "Source" means the code was cloned and read;
"README" means only the README or search snippets were reviewed.

## Two lanes

1. **Build lane:** teaching an agent to write apps that call Jev. Covered by the official
   `typesafe-ai/skills` (Claude Code plugin, `npx skills add` for other agents). Pin it; do
   not duplicate it.
2. **Runtime lane:** an agent reaching for Jev during its own work. This repo lives here.

## Runtime lane, by concern

### Fail semantics
| Repo | Depth | Behavior | Verdict |
| --- | --- | --- | --- |
| Gilbert09/jev-cli | Source | Guard fails closed to "ask"; screen/done/rank fail open; tests enforce both. Cache key uses the model alias string (alias moves do not invalidate within the 30-min TTL). | Reference design for the asymmetry. |
| shitianfang/jev-use | Source | Provider failure becomes "ask", but any exception inside the hook fails open. | Borrow the escalate contract, fix the exception path. |
| DoGMaTiiC/hermes-jev | Source | Enforce mode fails open on outage; caps latency because the Hermes loader fail-closes slow pre-tool hooks. Refuses redirects, caps payload, circuit breaker, honors Retry-After. | Borrow transport hardening only. |
| alexj11324/open-jev-approvals | Source | Fails open on API absence, timeouts, invalid responses. | Anti-pattern for gates. |
| TypeSafeAI/jev-harness | Source | Anything missing, malformed, unfavorable or below threshold becomes proposal-only; pins jev-1.13.0; labeled mock; honest about scripted numbers. **Community org** (created 2026-09-18), not official. | Best-in-class contract design. |
| chayan-bit/jev-harness | README | Bounded advisory over supplied options only; Codex hooks add context and deny only via your exact deny policy; digest-bound shadow judgments; held-out threshold calibration that never activates a policy. | Closest to our receipts/digest goals. |

### Runtime clients and skills
| Repo | Depth | Notes |
| --- | --- | --- |
| pedramamini gist `jev` | Source | Stdlib CLI plus SKILL.md for Claude Code, Codex, OpenCode. Measured pitfall: positional `items[i]` over 150 items gave 86/320 wrong vs 0/320 with keyed objects. Windows bug: `select.select` on a pipe throws, is swallowed, and falls through to a blocking read (the hang it tried to avoid). Bash-only installer. |
| kerpopule/hermes-jev-skills | Source | Per-turn model routing, memory filtering, compaction, skill selection for Hermes, Claude Code, Codex. OS secret store for the key. Defaults to jev-latest. |
| dfinke/Jev, dfinke/jev-experiments | Source | See `03-dfinke-jev-review.md`. |
| anasbekheit/typesafe-jev-mcp | Source | `TYPESAFE_API_KEY_COMMAND` fetches the key via a command (Credential Manager on Windows). Ships a .ps1 installer. |
| FrancoisChastel/jev-code | Source | Classify returns label, probabilities, margin and an auto/review decision. The fix for bare-label promotion. |
| RahulBalakavi/claude-code-jev | Source | Only gate that never references jev-latest. |
| shantanugoel/ask-jev-skill, tyroneross/typesafe-skills, ourines/hermes-jev | Source | Minor; eval script (shantanugoel) and evaluation playbook (tyroneross) worth a look. |
| ismaelsoilet/jev-harness | README | Append-only receipts with integrity hashes, `doctor` command, 160-case labeled corpus with replay, parity across Python, TypeScript and Rust. |
| AntonioCoppe/jev-harness | README | Decision harness with shadow mode, recipes and evals. |

### Skill routing (negative result)
- **shimo4228/jev-skill-router:** ported TypeSafe's skill-suggestion cookbook to a Claude
  Code prompt hook, then published that it does not help. The cookbook's gain (wrong-skill
  loads 16.8% to 7.3% over 488 requests) came from Haiku seeing descriptions truncated to 60
  characters. Claude Code shows full descriptions (up to 1,536 chars; median 412 in the
  author's repo). Real-session data: 3 sensible decisions of 6.
- himomohi/jev-skill-router: catalog outside context via one MCP tool; loads nothing when no
  candidate clears threshold. Defensible shape.
- aleksvega/jev-skill-router: routing plus a prompt-injection and dangerous-command audit of
  skill libraries. The audit is the useful part.
- lomeshdutta/skill-router, skillranker (abstention), jev-skillful (measures whether
  injection helped): README-level only.

### Evals and judging
- danielgshea/jev-as-a-judge, keduseworku/Jev-Calibration, sureshbujji/jev-eval-lab:
  see `04-calibration.md` and `05-mixture-of-agents.md`.

### LLM plus Jev in one loop (added 2026-09-25, README level)
- **browser-use/jev-ultrafast** (org account of the Browser Use project): a browser agent where
  Jev picks the operation and the target element from a live, numbered element table, and a
  small LLM writes text only when the operation is `TYPE_TEXT`. The target questions are
  speculative (one per operation type), so two decisions take one round trip. It is the
  clearest public example of "Jev decides, LLM generates" inside an agent loop. The Browser Use
  cloud waitlist is advertised in the README; treat its speed claims as marketing until read.
- **tamaratran/fast-jev-compaction**: a Claude Code plugin that replaces the compaction
  summary. It asks two Nouls per tool call (keep the call? keep the result verbatim?) and
  deletes or truncates instead of summarizing. It splits questions into concurrent requests to
  stay under the token limits, resending the full state with each. Relevant to `08` and to
  Claude Code hook work.
- Not pinned, for later: jaredpalmer/kev (open Jev-like decision models on Qwen), a possible
  degraded-mode or vendor-exit option next to `system-one-adapter-python`.
- GitHub star counts on Jev repos are very high for a 10-day-old ecosystem (tens of
  thousands). Do not treat stars as a signal of quality.

## Gaps nobody covers (our opportunity)

1. Windows and PowerShell agent integration (hooks, installers). Only dfinke's client exists.
2. Deterministic data-egress check before the call (query text with literals, connection
   strings from error logs).
3. Jev scoring multiple proposers with one shared question set, measured against a Self-MoA
   baseline.
4. Vendor exit: TypeSafe ships `system-one-adapter-python` (LLM-backed drop-in client). Nobody wires
   it in as a shadow comparator or degraded-mode fallback.

## Ecosystem noise

One awesome list counted 3,400+ projects within about 10 days of launch. Expect name
collisions (four repos named `jev-harness`, four named `jev-skill-router`).

# {{AGENT_FILE_NAME}}

> Routing file for AI coding agents. Keep this short (50-100 lines). Project-specific details go in `docs/`.

## Project

{{PROJECT_PURPOSE}}

## Quick Start

```bash
./init.sh                    # Install deps + run full verification
```

## Working Rules

1. **One feature at a time**: Pick exactly one unfinished feature from `feature_list.json`
2. **Verification required**: Don't claim done without running verification commands
3. **Update artifacts**: Before ending session, update `progress.md` and `feature_list.json`
4. **Stay in scope**: Don't modify files unrelated to the current feature
5. **Leave clean state**: Next session must be able to run `./init.sh` immediately

## Definition of Done

A feature is done only when ALL of the following are true:

- [ ] Target behavior is implemented
- [ ] Required verification actually ran (tests / lint / type-check)
- [ ] Evidence recorded in `feature_list.json` or `progress.md`
- [ ] Repository remains restartable from standard startup path

## End of Session

Before ending a session:

1. Update `progress.md` with current state
2. Update `feature_list.json` with new feature status
3. Record any unresolved risks or blockers
4. Commit with descriptive message once work is in safe state
5. Leave repo clean enough for next session to run `./init.sh` immediately

## Verification Commands

```bash
# Full verification (recommended)
{{PRIMARY_VERIFICATION_COMMAND}}
```

Required checks:
{{VERIFICATION_COMMANDS}}

## Escalation

If you encounter:
- **Architecture decisions**: Consult project architecture docs if present, otherwise ask user
- **Unclear requirements**: Check product/requirements docs if present, otherwise ask user
- **Repeated test failures**: Update progress, flag for human review
- **Scope ambiguity**: Re-read `feature_list.json` for definition of done

"""Eval cases for command canonicalization in the permission policy.

Covers the hex-escape and base64-pipe bypass vectors for the deny list,
plus the negative case (legitimate commands must still pass) and the
malformed-escape safety net (no exception leaks out of the check).
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class PermissionDenyBlocksHexEncodedRm(EvalCase):
    name = "permission-deny-blocks-hex-encoded-rm"
    description = "matches_deny blocks printf '\\x72\\x6d -rf /' (hex-obfuscated rm -rf /)"

    def run(self) -> EvalResult:
        from loom.agent.permissions import DEFAULT_POLICY

        obfuscated = "printf '\\x72\\x6d\\x20-rf\\x20/' | sh"
        matched = DEFAULT_POLICY.matches_deny(obfuscated)
        if matched != "rm -rf /":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 'rm -rf /', got {matched!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"hex-escaped command blocked via pattern {matched!r}",
        )


class PermissionDenyBlocksBase64PipeSh(EvalCase):
    name = "permission-deny-blocks-base64-pipe-sh"
    description = "matches_deny blocks 'echo <b64> | base64 -d | sh' (base64-decode-to-shell bypass)"

    def run(self) -> EvalResult:
        from loom.agent.permissions import DEFAULT_POLICY

        obfuscated = "echo cm0gLXJmIC90bXAvZm9vCg== |base64 -d|sh"
        matched = DEFAULT_POLICY.matches_deny(obfuscated)
        if matched is None or "base64" not in matched:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"base64-pipe-sh not blocked, got {matched!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"base64-pipe-sh blocked via pattern {matched!r}",
        )


class PermissionCanonicalizeDoesntBreakGit(EvalCase):
    name = "permission-canonicalize-doesnt-break-git"
    description = "matches_deny does NOT block legitimate 'git log --oneline' (no false positive)"

    def run(self) -> EvalResult:
        from loom.agent.permissions import DEFAULT_POLICY, _canonicalize

        benign = "git log --oneline"
        canonical = _canonicalize(benign)
        if canonical != benign:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"canonicalize mutated benign input: {benign!r} -> {canonical!r}",
            )
        matched = DEFAULT_POLICY.matches_deny(benign)
        if matched is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"benign git command blocked by {matched!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="git log --oneline unchanged and not blocked",
        )


class PermissionCanonicalizeHandlesMalformedEscapes(EvalCase):
    name = "permission-canonicalize-handles-malformed-escapes"
    description = "_canonicalize returns original string on malformed \\x escapes (safe-fail, no raise)"

    def run(self) -> EvalResult:
        from loom.agent.permissions import DEFAULT_POLICY, _canonicalize

        malformed = "echo \\xZZ"
        try:
            canonical = _canonicalize(malformed)
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"_canonicalize raised {type(exc).__name__}: {exc}",
            )
        if canonical is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="_canonicalize returned None on malformed input",
            )
        matched = DEFAULT_POLICY.matches_deny(malformed)
        if matched is not None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"malformed escape unexpectedly matched {matched!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"safe-fail on \\xZZ, canonical={canonical!r}",
        )
"""Presence-only token verifier for MCP server authentication."""

from fastmcp.server.auth import AccessToken, TokenVerifier


class PresenceTokenVerifier(TokenVerifier):
    """
    Simple token verifier that only checks token presence.

    Does not validate the token against any external service - downstream
    services (e.g., HuggingFace inference API) handle actual validation.
    """

    def __init__(
        self,
        provider: str = "generic",
        scopes: list[str] | None = None,
        *,
        base_url: str | None = None,
    ):
        """
        Initialize the presence token verifier.

        Args:
            provider: Name of the OAuth provider (for logging/debugging).
            scopes: List of scopes to assign to valid tokens. Defaults to ["access"].
            base_url: Optional protected resource base URL for auth metadata.
        """
        super().__init__(base_url=base_url, required_scopes=scopes or ["access"])
        self.provider = provider
        self.scopes = scopes or ["access"]

    async def verify_token(self, token: str) -> AccessToken | None:
        """
        Verify that a token is present (non-empty).

        Args:
            token: The bearer token to verify.

        Returns:
            AccessToken if token is present, None otherwise.
        """
        if not token or not token.strip():
            return None

        return AccessToken(
            token=token,
            client_id="bearer-client",
            scopes=self.scopes,
        )

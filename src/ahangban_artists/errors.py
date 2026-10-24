class ArtistCoverError(Exception):
    """Expected, user-actionable pipeline error."""


class ValidationError(ArtistCoverError):
    """Input or project data is invalid."""


class ExistingArtistError(ArtistCoverError):
    """An operation would overwrite an artist or an existing final."""


class QualityGateError(ArtistCoverError):
    """A technical quality gate failed."""

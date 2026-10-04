# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Provider-neutral identity, principal, context, and authorization contracts."""

from orbit.security.authorization import PolicyEngine, require_roles
from orbit.security.basic import BasicAuthenticator, BasicCredential
from orbit.security.bearer import BearerAuthenticator, TokenVerifier
from orbit.security.contracts import Authenticator
from orbit.security.identity import Identity
from orbit.security.jwks import JsonWebKey, JsonWebKeySet, JwksProvider
from orbit.security.oauth import (
    OAuthAuthorizationRequest,
    OAuthProvider,
    OAuthTokenResponse,
    OIDCDiscoveryDocument,
    OIDCDiscoveryProvider,
    is_https_url,
)
from orbit.security.principal import Principal
from orbit.security.ratelimit import RateLimiter, RateLimitResult
from orbit.security.tokens import Token, TokenRevocationStore
from orbit.security.validation import TokenValidationPolicy

__all__ = [
    "Identity",
    "JsonWebKey",
    "OIDCDiscoveryDocument",
    "OIDCDiscoveryProvider",
    "OAuthAuthorizationRequest",
    "OAuthProvider",
    "OAuthTokenResponse",
    "JsonWebKeySet",
    "JwksProvider",
    "BearerAuthenticator",
    "BasicAuthenticator",
    "BasicCredential",
    "Authenticator",
    "PolicyEngine",
    "Principal",
    "RateLimitResult",
    "RateLimiter",
    "Token",
    "TokenRevocationStore",
    "TokenVerifier",
    "TokenValidationPolicy",
    "require_roles",
    "is_https_url",
]

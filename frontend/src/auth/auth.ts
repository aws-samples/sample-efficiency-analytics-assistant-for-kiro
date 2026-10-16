/** Cognito Hosted UI OAuth2 authorization-code + PKCE flow.
 *
 * - login(): redirect to Hosted UI /authorize.
 * - handleCallback(): exchange the callback code for tokens (/oauth2/token), kept in memory.
 * - logout(): clear tokens + redirect to Hosted UI /logout.
 *
 * Tokens are kept in memory (avoids the XSS risk of localStorage, paired with CloudFront security headers);
 * a page refresh loses them, and the route guard re-runs sign-in when there is no token (silently returns if the Cognito session is still valid).
 */
import { getConfig } from "../config";
import { challengeFromVerifier, randomState, randomVerifier } from "./pkce";

const PKCE_KEY = "kiro_pkce_verifier";
const STATE_KEY = "kiro_oauth_state";

interface Tokens {
  idToken: string;
  accessToken: string;
  expiresAt: number; // epoch ms
  email?: string;
}

let _tokens: Tokens | null = null;

export function isAuthenticated(): boolean {
  return !!_tokens && _tokens.expiresAt > Date.now() + 5000;
}

export function getIdToken(): string | null {
  return isAuthenticated() ? _tokens!.idToken : null;
}

/** The access token is used for downstream calls that validate client_id (e.g. the AgentCore inbound JWT). */
export function getAccessToken(): string | null {
  return isAuthenticated() ? _tokens!.accessToken : null;
}

export function getUserEmail(): string | undefined {
  return _tokens?.email;
}

export async function login(): Promise<void> {
  const { cognito } = getConfig();
  const verifier = randomVerifier();
  const state = randomState();
  sessionStorage.setItem(PKCE_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const challenge = await challengeFromVerifier(verifier);
  const params = new URLSearchParams({
    response_type: "code",
    client_id: cognito.clientId,
    redirect_uri: cognito.redirectUri,
    scope: cognito.scopes.join(" "),
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${cognito.hostedUiDomain}/oauth2/authorize?${params}`);
}

export async function handleCallback(): Promise<void> {
  const { cognito } = getConfig();
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const savedState = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(PKCE_KEY);

  if (!code) throw new Error("Callback is missing the authorization code");
  if (!state || state !== savedState) throw new Error("State validation failed (possible CSRF)");
  if (!verifier) throw new Error("Missing PKCE verifier");

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cognito.clientId,
    code,
    redirect_uri: cognito.redirectUri,
    code_verifier: verifier,
  });

  const resp = await fetch(`${cognito.hostedUiDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) throw new Error(`Token exchange failed: ${resp.status} ${await resp.text()}`);
  const data = await resp.json();

  _tokens = {
    idToken: data.id_token,
    accessToken: data.access_token,
    expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
    email: decodeEmail(data.id_token),
  };
  sessionStorage.removeItem(PKCE_KEY);
  sessionStorage.removeItem(STATE_KEY);
}

export function logout(): void {
  const { cognito } = getConfig();
  _tokens = null;
  const params = new URLSearchParams({
    client_id: cognito.clientId,
    logout_uri: cognito.logoutUri,
  });
  window.location.assign(`${cognito.hostedUiDomain}/logout?${params}`);
}

function decodeEmail(idToken: string): string | undefined {
  try {
    const payload = idToken.split(".")[1];
    const json = JSON.parse(
      atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
    );
    return json.email as string | undefined;
  } catch {
    return undefined;
  }
}

/** Runtime config: loaded from public/config.json so the environment is not hardcoded into the build output.
 *  WebStack overwrites config.json with the real API/Cognito values at deploy time. */

export interface CognitoConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  hostedUiDomain: string;
  redirectUri: string;
  logoutUri: string;
  scopes: string[];
}

export interface AppConfig {
  apiEndpoint: string;
  cognito: CognitoConfig;
  creditUnitPrice: number;
}

let _cfg: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  if (_cfg) return _cfg;
  const resp = await fetch("/config.json", { cache: "no-store" });
  if (!resp.ok) throw new Error(`Failed to load runtime config config.json: ${resp.status}`);
  _cfg = (await resp.json()) as AppConfig;
  return _cfg;
}

export function getConfig(): AppConfig {
  if (!_cfg) throw new Error("Config not loaded yet; call loadConfig() first");
  return _cfg;
}

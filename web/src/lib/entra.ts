import "server-only";
import { readEntraSettings } from "./entra-flow";

/** The current settings, read from the environment each time so a restart is the only step. */
export function entraSettings() {
  return readEntraSettings(process.env);
}

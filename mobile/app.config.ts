/**
 * Layers build-time secrets over the static `app.json`.
 *
 * `google-services.json` (Firebase, needed for Android push) is supplied by the
 * owner and must not live in git, so EAS injects it as a file-type environment
 * variable (`eas env:create --name GOOGLE_SERVICES_JSON --type file`), whose value
 * is a path to the materialised file. Without it a release build registers no FCM
 * token and push silently never arrives, so a `production` Android build refuses
 * to start rather than ship that.
 */
type ExpoConfig = Record<string, any>;
type Env = Record<string, string | undefined>;

export function withGoogleServices(config: ExpoConfig, env: Env): ExpoConfig {
  const file = env.GOOGLE_SERVICES_JSON;
  if (file) {
    return { ...config, android: { ...config.android, googleServicesFile: file } };
  }
  if (env.EAS_BUILD_PROFILE === 'production' && env.EAS_BUILD_PLATFORM === 'android') {
    throw new Error(
      'GOOGLE_SERVICES_JSON is not set: a production Android build without google-services.json cannot receive push. ' +
        'See mobile/RELEASING.md, "Android push (FCM)".',
    );
  }
  return config;
}

export default ({ config }: { config: ExpoConfig }): ExpoConfig => withGoogleServices(config, process.env);

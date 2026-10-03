/** Project adapter: local fallback only; GitHub Actions supplies the release identity. */
module.exports = {
  repository: 'SCVN-Zee/unity-asset-library',
  appMetadata: true,
  assets: ({ version }) => [
    `release/unity-asset-library-${version}-arm64.dmg`,
    `release/unity-asset-library-${version}-arm64.zip`,
  ],
  notes: 'macOS Apple Silicon (arm64). Python and dependencies are included. This app is ad-hoc signed, not notarized. Install in Applications. If macOS blocks first launch, use System Settings > Privacy & Security > Open Anyway. Stable updates are SHA-256 verified and installed after a confirmed restart.',
};

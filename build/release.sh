#!/bin/bash
# Usage: ./build/release.sh v1.0.48
# Pushes the current branch and creates + pushes the version tag so GitHub builds the EXE.
set -e

VERSION=$1
if [ -z "$VERSION" ]; then
  echo "Usage: $0 v1.0.XX"
  exit 1
fi

git push origin main
git tag "$VERSION"
git push origin "$VERSION"

echo ""
echo "✅ Released $VERSION — GitHub is building the Mac and Windows apps."
echo "   Watch progress: https://github.com/Keeksawy/Wtsp-blast/actions"

#!/bin/bash
# Run complete semantic pipeline sequentially with caffeinate (no Mac sleep).
# Usage: ./scripts/run_pipeline.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

echo "🔄 Starting semantic pipeline (1.5h total)..."
echo "   Preventing Mac sleep with caffeinate -i"
echo ""

# Use caffeinate to prevent sleep during pipeline
caffeinate -i bash << 'EOF'
set -e

echo "📊 Phase 1: GDELT anomalies (Goldstein z-score)"
uv run pathos ingest gdelt-anomalies --backfill-country --full
echo "✅ gdelt-anomalies complete"
echo ""

echo "🧠 Phase 2: Embedding (multilingual-e5-small)"
uv run pathos embed
echo "✅ embed complete"
echo ""

echo "🔍 Phase 3: NER + Geocoding + Wikidata"
uv run pathos extract
echo "✅ extract complete"
echo ""

echo "🎯 Phase 4: Clustering (Union-find → events)"
uv run pathos cluster
echo "✅ cluster complete"
echo ""

echo "🕸️  Phase 5: Entity graph + narrative divergence"
uv run pathos graph
echo "✅ graph complete"
echo ""

echo "🎉 Pipeline complete!"
EOF

# Regenerate the web app's API types (API-7):
#   1. backend/openapi.json from the code (repoatlas api openapi);
#   2. frontend/src/lib/api/schema.d.ts from that spec, with the openapi-typescript version
#      pinned in frontend/package-lock.json (installed with `npm ci` when missing).
# Commit both files. With -Check, nothing is written: the script fails if either is stale.
#
# Usage: scripts/gen_api_types.ps1 [-Check]        (Linux and macOS: scripts/gen_api_types.sh)
param([switch]$Check)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$types = "src/lib/api/schema.d.ts"

function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

Push-Location $root
try {
    if (-not (Test-Path "frontend/node_modules/.bin/openapi-typescript")) {
        Invoke-Native "npm ci" { npm --prefix frontend ci --no-audit --no-fund }
    }

    Set-Location (Join-Path $root "backend")
    if ($Check) {
        & uv run repoatlas api openapi --out openapi.json --check
        if ($LASTEXITCODE -ne 0) { exit 1 }
    } else {
        Invoke-Native "repoatlas api openapi" { uv run repoatlas api openapi --out openapi.json }
    }

    Set-Location (Join-Path $root "frontend")
    if ($Check) {
        $fresh = ".schema.d.ts.check"
        Invoke-Native "openapi-typescript" {
            npx --no openapi-typescript ../backend/openapi.json --output $fresh
        }
        $same = (Get-FileHash $fresh).Hash -eq (Get-FileHash $types).Hash
        Remove-Item $fresh
        if (-not $same) {
            Write-Error "frontend/$types is out of date. Run: scripts/gen_api_types.ps1" -ErrorAction Continue
            exit 1
        }
        Write-Output "frontend/$types is up to date."
    } else {
        Invoke-Native "openapi-typescript" {
            npx --no openapi-typescript ../backend/openapi.json --output $types
        }
    }
} finally {
    Pop-Location
}

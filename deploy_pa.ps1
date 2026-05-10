# deploy_pa.ps1
# Deploys the latest code to the live PythonAnywhere site by:
#   1. Calling the /deploy webhook on the live site — this makes the server
#      pull the latest code from GitHub by running "git pull"
#   2. Reloading the web app via the PythonAnywhere API so the new code
#      is picked up by the live site
#
# Called automatically by deploy.bat — you don't need to run this directly.

param(
    [string]$Token,        # PythonAnywhere API token
    [string]$DeploySecret  # Secret that protects the /deploy webhook
)

$username = "gadge64"
$domain   = "gadge64.pythonanywhere.com"

# The Authorization header identifies us to the PythonAnywhere API
$headers = @{ Authorization = "Token $Token" }

# ── Step 1: Call the /deploy webhook on the live site ─────────────────────────
# The Flask app has a hidden /deploy route that runs "git pull" when called
# with the correct secret. This is more reliable than using the consoles API.
Write-Host "[2/3] Triggering git pull on live server..."

try {
    $pullResult = Invoke-RestMethod `
        -Uri    "https://$domain/deploy?secret=$DeploySecret" `
        -Method GET

    Write-Host "      Server response: $pullResult"
} catch {
    Write-Host "      WARNING: Deploy webhook failed: $($_.Exception.Message)"
    Write-Host "      The site will still be reloaded but may not have the latest code."
}

# Small pause to make sure the git pull is fully done before we reload
Start-Sleep -Seconds 3

# ── Step 2: Reload the live website via the PythonAnywhere API ────────────────
# This restarts the Python web process so it loads the newly pulled code.
# Without this step the site would keep running the old version.
Write-Host "[3/3] Reloading live website..."

try {
    Invoke-RestMethod `
        -Uri     "https://www.pythonanywhere.com/api/v0/user/$username/webapps/$domain/reload/" `
        -Method  POST `
        -Headers $headers | Out-Null

    Write-Host "      Website reloaded successfully."
} catch {
    Write-Host "      ERROR reloading website: $($_.Exception.Message)"
}

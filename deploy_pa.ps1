# deploy_pa.ps1
# This script talks to PythonAnywhere using their API to:
#   1. Pull the latest code from GitHub onto the live server
#   2. Reload the website so the changes take effect
#
# It is called automatically by deploy.bat — you don't need to run it directly.

param(
    [string]$Token  # The PythonAnywhere API token, passed in from deploy.bat
)

# Settings for the PythonAnywhere account
$username   = "gadge64"
$domain     = "gadge64.pythonanywhere.com"
$projectDir = "/home/gadge64/turbine-tracker"

# The Authorization header is how PythonAnywhere knows who is making the request
$headers = @{ Authorization = "Token $Token" }

# ── Step 1: Open a bash console on PythonAnywhere and run "git pull" ──────────
# This tells the live server to download the latest code we just pushed to GitHub.
# The PythonAnywhere API lets us run shell commands remotely via their console API.
Write-Host "[2/3] Pulling latest code onto PythonAnywhere server..."

$body = @{
    executable        = "bash"
    # -c means "run this string as a shell command"
    arguments         = "-c `"cd $projectDir && git pull`""
    working_directory = $projectDir
} | ConvertTo-Json  # Convert the PowerShell object to JSON format for the API

try {
    Invoke-RestMethod `
        -Uri         "https://www.pythonanywhere.com/api/v0/user/$username/consoles/" `
        -Method      POST `
        -Headers     $headers `
        -ContentType "application/json" `
        -Body        $body | Out-Null   # | Out-Null suppresses the raw API response from printing

    Write-Host "      Git pull triggered. Waiting 12 seconds for it to finish..."
} catch {
    Write-Host "      WARNING: Could not trigger git pull: $($_.Exception.Message)"
}

# Wait for the git pull to finish on the remote server before reloading
Start-Sleep -Seconds 12

# ── Step 2: Reload the live website ───────────────────────────────────────────
# "Reloading" restarts the Python web process so it picks up the new code.
# Without this step the site would still be running the old version.
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

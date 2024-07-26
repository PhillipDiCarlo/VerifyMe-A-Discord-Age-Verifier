param (
    [string]$version
)

if (-not $version) {
    Write-Host "Please provide a version number as a parameter."
    exit 1
}

# Tag the images
docker tag verifyme-subscription-manager italiandogs/verifyme-subscription-manager:$version
docker tag verifyme-stripe-webhook italiandogs/verifyme-stripe-webhook:$version
docker tag verifyme-discord-bot italiandogs/verifyme-discord-bot:$version
docker tag verifyme-subscription-manager italiandogs/verifyme-subscription-manager:latest
docker tag verifyme-stripe-webhook italiandogs/verifyme-stripe-webhook:latest
docker tag verifyme-discord-bot italiandogs/verifyme-discord-bot:latest
Write-Output "All tagging done"

# Push the images with the specified version
docker push italiandogs/verifyme-discord-bot:$version
docker push italiandogs/verifyme-stripe-webhook:$version
docker push italiandogs/verifyme-subscription-manager:$version

# Push the images with the latest tag
docker push italiandogs/verifyme-discord-bot:latest
docker push italiandogs/verifyme-stripe-webhook:latest
docker push italiandogs/verifyme-subscription-manager:latest
Write-Output "All pushing done"
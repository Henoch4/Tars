@echo off
title Vercel Relink + Deploy - TARS
color 0E
echo ============================================================
echo  VERCEL RELINK - FIX AUG 29 STALE DEPLOY
echo  Repo: Henoch4/Tars  Branch: main  Project: tars
echo  Current live is stale (5 endpoints, Aug 29)
echo ============================================================
echo.
echo  This will:
echo   1. Install Vercel CLI globally (npm i -g vercel)
echo   2. Link local folder to Vercel project
echo   3. Deploy current commit 78571c7 to prod
echo   4. Verify https://api.tarstrade.xyz/manifest flips to 24 endpoints
echo.
pause
echo.
echo [1/4] Installing Vercel CLI...
call npm i -g vercel
if errorlevel 1 (
  echo FAILED: npm not found. Install Node.js from https://nodejs.org then re-run.
  pause
  exit /b 1
)
echo Vercel version:
call vercel --version
echo.
echo [2/4] Linking project (select Henoch4/Tars when prompted)...
echo    Scope: Henoch4
echo    Project: tars (or Tarstrade)
echo    Link to existing project: YES
echo.
call vercel link
if errorlevel 1 (
  echo Link failed. Try: vercel link --project tars --yes
  pause
  exit /b 1
)
echo.
echo [3/4] Deploying to production...
echo    This deploys HEAD (78571c7) with manifest 24 endpoints
echo.
call vercel --prod --yes
if errorlevel 1 (
  echo Deploy failed. Check Vercel dashboard for logs.
  pause
  exit /b 1
)
echo.
echo [4/4] Verifying live manifest (wait 15s for propagation)...
timeout /t 15 >nul
echo.
echo --- https://api.tarstrade.xyz/manifest ---
curl -s https://api.tarstrade.xyz/manifest
echo.
echo.
echo --- health ---
curl -s https://api.tarstrade.xyz/health
echo.
echo.
echo --- agent-card (should be 200 after deploy) ---
curl -s https://api.tarstrade.xyz/.well-known/agent-card.json
echo.
echo.
echo --- pricing (should be 200 after deploy) ---
curl -s https://api.tarstrade.xyz/api/v1/pricing
echo.
echo ============================================================
echo  EXPECT AFTER DEPLOY:
echo   manifest.name = "TARS - Trade Audit & Risk System"
echo   endpoints = 24 (not 5)
echo   agent-card.json = 200 (not 404)
echo   pricing = 200 (not 404)
echo  If still 5 endpoints, check Vercel Dashboard -^> Deployments -^> Latest logs
echo  and Settings -^> Git -^> Connected Repository = Henoch4/Tars, Branch = main
echo ============================================================
pause

@echo off
title TARS OKX ASP Listing - Human Handoff
color 0A
echo ============================================================
echo  TARS - Trade Audit ^& Risk System - OKX ASP Handoff
echo  Repo: Henoch4/Tars  Commit: 78571c7  Manifest: TARS 24 endpoints
echo ============================================================
echo.
echo  [1] WAIT FOR VERCEL DEPLOY (~90s after git push)
echo      Dashboard: https://vercel.com/dashboard
echo      Pushed: 78571c7 chore: restore ASP manifest health -^> origin/main
echo.
echo  [2] VERIFY LIVE ENDPOINTS (run these curls)
echo      curl https://api.tarstrade.xyz/manifest ^| jq .name,.version
echo      curl https://api.tarstrade.xyz/health
echo      curl https://api.tarstrade.xyz/.well-known/agent-card.json ^| jq .name,.tools
echo      curl https://api.tarstrade.xyz/.well-known/x402
echo      curl https://api.tarstrade.xyz/api/v1/pricing ^| jq .routes
echo      EXPECT: 200 on all, agent-card lists paid tools hire/trade $0.50 premium
echo.
echo  [3] OKX.AI ASP REGISTRATION
echo      URL: https://okx.ai  ^> ASP / Agent Marketplace ^> Register
echo      Manifest URL: https://api.tarstrade.xyz/manifest
echo      Service: TARS Trading Audit Service - https://api.tarstrade.xyz/trade
echo      Alt endpoint: https://api.tarstrade.xyz/hire (portfolio audit)
echo      Chain: xlayer (X Layer testnet chainId 1952)
echo      Contract: 0x6019b96e9d0Ba17588eb22579d9c2dEf0473d07c
echo      Category: Finance  Version: 0.1.0
echo.
echo  [4] TEST HIRE/TRADE (dry_run=true, no token needed while DRY_RUN=true)
echo      curl -X POST https://api.tarstrade.xyz/trade -H "Content-Type: application/json" -d "{\"assets\":[\"BTC-USDT-SWAP\"],\"dry_run\":true}"
echo      curl -X POST https://api.tarstrade.xyz/hire -H "Content-Type: application/json" -d "{\"mode\":\"own_account\",\"profile_mode\":\"demo\",\"balance_data\":{\"data\":[{\"ccy\":\"USDT\",\"availBal\":\"10000\"}]},\"positions_data\":[]}"
echo.
echo  [5] SUBMISSION CHECKLIST (HACKATHON_SUBMISSION.md)
echo      [x] manifest doctor: healthy (24 endpoints)
echo      [x] Keys rotated 2026-09-12 in .env (old 0x4E80... invalidated)
echo      [x] HACKATHON_SUBMISSION.md:13 path fixed (Tarstrade root)
echo      [ ] Vercel live verified (step 2)
echo      [ ] okx.ai listing submitted
echo      [ ] X post @AuditTrailTrade
echo.
echo ============================================================
echo  Press any key to run live checks now...
pause >nul
echo.
echo  Running checks...
curl -s https://api.tarstrade.xyz/manifest | findstr "name"
echo.
curl -s https://api.tarstrade.xyz/health
echo.
echo  Done. Keep this window open for reference.
pause

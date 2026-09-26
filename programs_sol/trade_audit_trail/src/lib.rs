use anchor_lang::prelude::*;

declare_id!("516a5KdUr5oLJTVQZaiDWxqgRSQ1xPHSvFoQbCmwVtRS");

#[program]
pub mod trade_audit_trail {
    use super::*;

    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        let agent_state = &mut ctx.accounts.agent_state;
        agent_state.agent = ctx.accounts.agent.key();
        agent_state.max_position_usd = 0;
        agent_state.max_daily_loss_usd = 0;
        agent_state.max_leverage_bps = 0;
        agent_state.min_confidence_bps = 0;
        agent_state.daily_loss = 0;
        agent_state.daily_trades = 0;
        agent_state.kill_switch = false;
        agent_state.bump = ctx.bumps.agent_state;
        Ok(())
    }

    pub fn set_risk_params(
        ctx: Context<SetRiskParams>,
        max_position_usd: u64,
        max_daily_loss_usd: u64,
        max_leverage_bps: u64,
        min_confidence_bps: u64,
    ) -> Result<()> {
        let agent_state = &mut ctx.accounts.agent_state;

        // Tightening only
        if agent_state.max_position_usd > 0 {
            require!(max_position_usd <= agent_state.max_position_usd, ErrorCode::PosTooHigh);
            require!(max_daily_loss_usd <= agent_state.max_daily_loss_usd, ErrorCode::LossTooHigh);
            require!(max_leverage_bps <= agent_state.max_leverage_bps, ErrorCode::LevTooHigh);
            require!(min_confidence_bps >= agent_state.min_confidence_bps, ErrorCode::ConfTooLow);
        }

        agent_state.max_position_usd = max_position_usd;
        agent_state.max_daily_loss_usd = max_daily_loss_usd;
        agent_state.max_leverage_bps = max_leverage_bps;
        agent_state.min_confidence_bps = min_confidence_bps;

        Ok(())
    }

    pub fn log_decision(
        ctx: Context<LogDecision>,
        decision_id: [u8; 32],
        package_id: [u8; 32],
        asset: String,
        signal: String,
        strategy: String,
        confidence: i64,
        entry_price: u64,
        size_usd: u64,
        risk_hash: [u8; 32],
    ) -> Result<()> {
        let agent_state = &mut ctx.accounts.agent_state;
        let decision = &mut ctx.accounts.decision;

        // Kill switch check
        require!(!agent_state.kill_switch, ErrorCode::KillSwitchActive);

        // Risk checks
        require!(size_usd <= agent_state.max_position_usd, ErrorCode::TooLarge);
        require!(confidence >= 0, ErrorCode::NegConfidence);
        require!(confidence as u64 >= agent_state.min_confidence_bps, ErrorCode::BelowMinConf);

        // Daily counters
        let today = Clock::get()?.unix_timestamp / 86400;
        if today > agent_state.daily_bucket {
            agent_state.daily_bucket = today;
            agent_state.daily_trades = 0;
            agent_state.daily_loss = 0;
        }
        require!(agent_state.daily_trades < 100, ErrorCode::MaxTradesExceeded);
        require!(agent_state.daily_loss < agent_state.max_daily_loss_usd, ErrorCode::DailyLossLimit);

        agent_state.daily_trades += 1;

        decision.decision_id = decision_id;
        decision.package_id = package_id;
        decision.agent = ctx.accounts.agent.key();
        decision.asset = asset.clone();
        decision.signal = signal.clone();
        decision.strategy = strategy;
        decision.confidence = confidence;
        decision.entry_price = entry_price;
        decision.size_usd = size_usd;
        decision.slot = Clock::get()?.slot;
        decision.executed = false;
        decision.is_short = signal == "SHORT" || signal == "SELL";
        decision.risk_hash = risk_hash;

        agent_state.daily_volume = agent_state.daily_volume.checked_add(size_usd).unwrap();

        emit!(DecisionLogged {
            decision_id,
            package_id,
            agent: ctx.accounts.agent.key(),
            asset,
            signal,
            confidence,
            size_usd,
            risk_hash,
        });

        Ok(())
    }

    pub fn record_execution(
        ctx: Context<RecordExecution>,
        decision_id: [u8; 32],
        fill_price: u64,
        fill_size_usd: u64,
        fee_usd: u64,
        success: bool,
    ) -> Result<()> {
        let decision = &mut ctx.accounts.decision;
        require!(decision.decision_id == decision_id, ErrorCode::DecisionMismatch);
        require!(!decision.executed, ErrorCode::AlreadyExecuted);
        require!(decision.agent == ctx.accounts.agent.key(), ErrorCode::NotDecisionOwner);

        decision.executed = true;

        if success {
            let loss = if !decision.is_short && fill_price < decision.entry_price {
                let diff = decision.entry_price - fill_price;
                (diff as u128 * decision.size_usd as u128 / 1e8 as u128) as u64
            } else if decision.is_short && fill_price > decision.entry_price {
                let diff = fill_price - decision.entry_price;
                (diff as u128 * decision.size_usd as u128 / 1e8 as u128) as u64
            } else { 0 };

            if loss > 0 {
                let agent_state = &mut ctx.accounts.agent_state;
                agent_state.daily_loss = agent_state.daily_loss.checked_add(loss).unwrap();
            }
        }

        emit!(ExecutionRecorded {
            decision_id,
            fill_price,
            fill_size_usd,
            fee_usd,
            success,
        });

        Ok(())
    }

    pub fn activate_kill_switch(ctx: Context<KillSwitch>, reason: String) -> Result<()> {
        let agent_state = &mut ctx.accounts.agent_state;
        agent_state.kill_switch = true;
        emit!(KillSwitchActivated { agent: ctx.accounts.agent.key(), reason, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    pub fn deactivate_kill_switch(ctx: Context<KillSwitch>) -> Result<()> {
        let agent_state = &mut ctx.accounts.agent_state;
        agent_state.kill_switch = false;
        emit!(KillSwitchDeactivated { agent: ctx.accounts.agent.key(), timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = agent,
        space = 8 + AgentState::INIT_SPACE,
        seeds = [b"agent_state", agent.key().as_ref()],
        bump
    )]
    pub agent_state: Account<'info, AgentState>,
    #[account(mut)]
    pub agent: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SetRiskParams<'info> {
    #[account(
        mut,
        seeds = [b"agent_state", agent.key().as_ref()],
        bump = agent_state.bump,
        has_one = agent
    )]
    pub agent_state: Account<'info, AgentState>,
    pub agent: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(decision_id: [u8; 32])]
pub struct LogDecision<'info> {
    #[account(
        mut,
        seeds = [b"agent_state", agent.key().as_ref()],
        bump = agent_state.bump,
        has_one = agent
    )]
    pub agent_state: Account<'info, AgentState>,
    #[account(
        init,
        payer = agent,
        space = 8 + Decision::INIT_SPACE,
        seeds = [b"decision", decision_id.as_ref()],
        bump
    )]
    pub decision: Account<'info, Decision>,
    #[account(mut)]
    pub agent: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(decision_id: [u8; 32])]
pub struct RecordExecution<'info> {
    #[account(
        mut,
        seeds = [b"decision", decision_id.as_ref()],
        bump,
        has_one = agent
    )]
    pub decision: Account<'info, Decision>,
    #[account(mut)]
    pub agent_state: Account<'info, AgentState>,
    pub agent: Signer<'info>,
}

#[derive(Accounts)]
pub struct KillSwitch<'info> {
    #[account(
        mut,
        seeds = [b"agent_state", agent.key().as_ref()],
        bump = agent_state.bump,
        has_one = agent
    )]
    pub agent_state: Account<'info, AgentState>,
    pub agent: Signer<'info>,
}

#[account]
#[derive(InitSpace)]
pub struct AgentState {
    pub agent: Pubkey,
    pub max_position_usd: u64,
    pub max_daily_loss_usd: u64,
    pub max_leverage_bps: u64,
    pub min_confidence_bps: u64,
    pub daily_loss: u64,
    pub daily_trades: u64,
    pub daily_volume: u64,
    pub daily_bucket: i64,
    pub kill_switch: bool,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct Decision {
    pub decision_id: [u8; 32],
    pub package_id: [u8; 32],
    pub agent: Pubkey,
    #[max_len(32)]
    pub asset: String,
    #[max_len(16)]
    pub signal: String,
    #[max_len(32)]
    pub strategy: String,
    pub confidence: i64,
    pub entry_price: u64,
    pub size_usd: u64,
    pub slot: u64,
    pub executed: bool,
    pub is_short: bool,
    pub risk_hash: [u8; 32],
}

#[event]
pub struct DecisionLogged {
    pub decision_id: [u8; 32],
    pub package_id: [u8; 32],
    pub agent: Pubkey,
    pub asset: String,
    pub signal: String,
    pub confidence: i64,
    pub size_usd: u64,
    pub risk_hash: [u8; 32],
}

#[event]
pub struct ExecutionRecorded {
    pub decision_id: [u8; 32],
    pub fill_price: u64,
    pub fill_size_usd: u64,
    pub fee_usd: u64,
    pub success: bool,
}

#[event]
pub struct KillSwitchActivated {
    pub agent: Pubkey,
    pub reason: String,
    pub timestamp: i64,
}

#[event]
pub struct KillSwitchDeactivated {
    pub agent: Pubkey,
    pub timestamp: i64,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Kill switch active")]
    KillSwitchActive,
    #[msg("Position too large")]
    TooLarge,
    #[msg("Negative confidence")]
    NegConfidence,
    #[msg("Below min confidence")]
    BelowMinConf,
    #[msg("Max trades exceeded")]
    MaxTradesExceeded,
    #[msg("Daily loss limit exceeded")]
    DailyLossLimit,
    #[msg("Decision mismatch")]
    DecisionMismatch,
    #[msg("Already executed")]
    AlreadyExecuted,
    #[msg("Not decision owner")]
    NotDecisionOwner,
    #[msg("Position too high")]
    PosTooHigh,
    #[msg("Loss too high")]
    LossTooHigh,
    #[msg("Leverage too high")]
    LevTooHigh,
    #[msg("Confidence too low")]
    ConfTooLow,
}
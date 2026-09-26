use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Mint};

declare_id!("Gd7Ciu6KgPwoajZZgAUNethJAFNe4s3nhJV64XNRz9aF");

#[program]
pub mod trading_vault {
    use super::*;

    pub fn initialize(
        ctx: Context<Initialize>,
        min_deposit: u64,
        max_tvl: u64,
        attest_timelock: u64,
        max_attestation_delta_bps: u64,
    ) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        vault.owner = ctx.accounts.owner.key();
        vault.agent = ctx.accounts.agent.key();
        vault.mint = ctx.accounts.mint.key();
        vault.min_deposit = min_deposit;
        vault.max_tvl = max_tvl;
        vault.attest_timelock = attest_timelock;
        vault.max_attestation_delta_bps = max_attestation_delta_bps;
        vault.total_assets = 0;
        vault.total_shares = 0;
        vault.package_open = false;
        vault.bump = ctx.bumps.vault;
        Ok(())
    }

    pub fn deposit(ctx: Context<Deposit>, amount: u64) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        require!(amount >= vault.min_deposit, ErrorCode::DepositTooSmall);
        require!(vault.total_assets.checked_add(amount).unwrap() <= vault.max_tvl, ErrorCode::MaxTvlExceeded);

        let shares = if vault.total_shares == 0 {
            amount
        } else {
            (amount as u128 * vault.total_shares as u128 / vault.total_assets as u128) as u64
        };
        require!(shares > 0, ErrorCode::InvalidAmount);

        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                token::Transfer {
                    from: ctx.accounts.user_token_account.to_account_info(),
                    to: ctx.accounts.vault_token_account.to_account_info(),
                    authority: ctx.accounts.user.to_account_info(),
                },
            ),
            amount,
        )?;

        token::mint_to(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                token::MintTo {
                    mint: ctx.accounts.mint.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: vault.to_account_info(),
                },
                &[&[b"vault", vault.owner.as_ref(), &[vault.bump]]],
            ),
            shares,
        )?;

        vault.total_assets = vault.total_assets.checked_add(amount).unwrap();
        vault.total_shares = vault.total_shares.checked_add(shares).unwrap();

        emit!(Deposited { user: ctx.accounts.user.key(), amount, shares });
        Ok(())
    }

    pub fn withdraw(ctx: Context<Withdraw>, shares: u64) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        require!(!vault.package_open, ErrorCode::PackageOpen);
        require!(shares > 0, ErrorCode::InvalidAmount);
        require!(shares <= ctx.accounts.user_token_account.amount, ErrorCode::InvalidAmount);

        let amount = (shares as u128 * vault.total_assets as u128 / vault.total_shares as u128) as u64;

        token::burn(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                token::Burn {
                    mint: ctx.accounts.mint.to_account_info(),
                    from: ctx.accounts.user_token_account.to_account_info(),
                    authority: ctx.accounts.user.to_account_info(),
                },
            ),
            shares,
        )?;

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                token::Transfer {
                    from: ctx.accounts.vault_token_account.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: vault.to_account_info(),
                },
                &[&[b"vault", vault.owner.as_ref(), &[vault.bump]]],
            ),
            amount,
        )?;

        vault.total_assets = vault.total_assets.checked_sub(amount).unwrap();
        vault.total_shares = vault.total_shares.checked_sub(shares).unwrap();

        emit!(Withdrawn { user: ctx.accounts.user.key(), amount, shares });
        Ok(())
    }

    pub fn attest_total_assets(ctx: Context<AttestTotalAssets>, new_total_assets: u64) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        require!(ctx.accounts.agent.key() == vault.agent, ErrorCode::OnlyAgent);
        require!(new_total_assets <= vault.max_tvl, ErrorCode::MaxTvlExceeded);

        let now = Clock::get()?.unix_timestamp;
        require!(
            now - vault.last_attestation >= vault.attest_timelock as i64,
            ErrorCode::AttestationTooSoon
        );

        if vault.total_assets > 0 {
            let delta = if new_total_assets > vault.total_assets {
                new_total_assets - vault.total_assets
            } else {
                vault.total_assets - new_total_assets
            };
            let max_delta = (vault.total_assets as u128 * vault.max_attestation_delta_bps as u128 / 10000) as u64;
            require!(delta <= max_delta, ErrorCode::AttestationDeltaTooLarge);
        }

        vault.total_assets = new_total_assets;
        vault.last_attestation = Clock::get()?.unix_timestamp;

        emit!(NavAttested { total_assets: new_total_assets, timestamp: vault.last_attestation });
        Ok(())
    }

    pub fn set_package_open(ctx: Context<SetPackageOpen>, open: bool) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        require!(ctx.accounts.agent.key() == vault.agent, ErrorCode::OnlyAgent);
        vault.package_open = open;
        emit!(PackageOpenChanged { open, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = owner,
        space = 8 + Vault::INIT_SPACE,
        seeds = [b"vault", owner.key().as_ref()],
        bump
    )]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub agent: Signer<'info>,
    pub mint: Account<'info, Mint>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

#[derive(Accounts)]
pub struct Deposit<'info> {
    #[account(
        mut,
        seeds = [b"vault", vault.owner.as_ref()],
        bump = vault.bump,
        has_one = mint
    )]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub user: Signer<'info>,
    #[account(mut)]
    pub user_token_account: Account<'info, TokenAccount>,
    // init_if_needed: first deposit creates the vault's token account; later
    // deposits reuse it. Address + mint + authority are all constrained, so
    // a pre-existing account can only be the genuine vault account.
    #[account(
        init_if_needed,
        payer = user,
        seeds = [b"vault_token", vault.key().as_ref()],
        bump,
        token::mint = mint,
        token::authority = vault,
    )]
    pub vault_token_account: Account<'info, TokenAccount>,
    pub mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Withdraw<'info> {
    #[account(
        mut,
        seeds = [b"vault", vault.owner.as_ref()],
        bump = vault.bump,
        has_one = mint
    )]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub user: Signer<'info>,
    #[account(mut)]
    pub user_token_account: Account<'info, TokenAccount>,
    #[account(
        mut,
        seeds = [b"vault_token", vault.key().as_ref()],
        bump
    )]
    pub vault_token_account: Account<'info, TokenAccount>,
    pub mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct AttestTotalAssets<'info> {
    #[account(
        mut,
        seeds = [b"vault", vault.owner.as_ref()],
        bump = vault.bump
    )]
    pub vault: Account<'info, Vault>,
    pub agent: Signer<'info>,
}

#[derive(Accounts)]
pub struct SetPackageOpen<'info> {
    #[account(
        mut,
        seeds = [b"vault", vault.owner.as_ref()],
        bump = vault.bump
    )]
    pub vault: Account<'info, Vault>,
    pub agent: Signer<'info>,
}

#[account]
#[derive(InitSpace)]
pub struct Vault {
    pub owner: Pubkey,
    pub agent: Pubkey,
    pub mint: Pubkey,
    pub min_deposit: u64,
    pub max_tvl: u64,
    pub attest_timelock: u64,
    pub max_attestation_delta_bps: u64,
    pub total_assets: u64,
    pub total_shares: u64,
    pub package_open: bool,
    pub last_attestation: i64,
    pub bump: u8,
}

#[event]
pub struct Deposited {
    pub user: Pubkey,
    pub amount: u64,
    pub shares: u64,
}

#[event]
pub struct Withdrawn {
    pub user: Pubkey,
    pub amount: u64,
    pub shares: u64,
}

#[event]
pub struct NavAttested {
    pub total_assets: u64,
    pub timestamp: i64,
}

#[event]
pub struct PackageOpenChanged {
    pub open: bool,
    pub timestamp: i64,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Deposit too small")]
    DepositTooSmall,
    #[msg("Max TVL exceeded")]
    MaxTvlExceeded,
    #[msg("Invalid amount")]
    InvalidAmount,
    #[msg("Only agent can call")]
    OnlyAgent,
    #[msg("Attestation delta too large")]
    AttestationDeltaTooLarge,
    #[msg("Package open - withdrawals blocked")]
    PackageOpen,
    #[msg("Attestation too soon - timelock not elapsed")]
    AttestationTooSoon,
}
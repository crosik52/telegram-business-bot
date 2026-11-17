"""Immutable complete schema baseline for an EMPTY Save Log PostgreSQL database.

Legacy databases must use their original migration history. No stamp/conversion
or destructive downgrade is provided by this separate fresh-database path.
"""
import sqlalchemy as sa

from alembic import op

revision = "clean_20261011_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail closed on previously initialized/unversioned schemas. Offline SQL
    # retains the same guard; pg advisory lock serializes accidental two runners.
    op.execute("SELECT pg_advisory_xact_lock(817260412)")
    op.execute("""
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema()
          AND c.relkind IN ('r','p','v','m','S','f')
          AND c.relname <> 'alembic_version'
    ) OR EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname=current_schema() AND t.typtype='e'
    ) THEN
        RAISE EXCEPTION 'Clean baseline requires an empty schema; existing objects are never modified';
    END IF;
END $$;
""")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('admin_action_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('admin_username', sa.String(length=255), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_owner_telegram_id', sa.BigInteger(), nullable=True),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_admin_action_log_created_at', 'admin_action_log', ['created_at'], unique=False)
    op.create_table('ai_analysis_cache',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.BigInteger(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('result_json', sa.Text(), nullable=False),
    sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ai_analysis_cache_owner_chat', 'ai_analysis_cache', ['owner_id', 'chat_id'], unique=True)
    op.create_table('ai_analysis_daily_counts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.BigInteger(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('count', sa.SmallInteger(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ai_analysis_daily_counts_owner_date', 'ai_analysis_daily_counts', ['owner_id', 'date'], unique=True)
    op.create_table('analysis_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('owner_id', sa.BigInteger(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('result_json', sa.Text(), nullable=False),
    sa.Column('stored_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analysis_results_chat_id'), 'analysis_results', ['chat_id'], unique=False)
    op.create_index('ix_analysis_results_owner_chat', 'analysis_results', ['owner_id', 'chat_id'], unique=True)
    op.create_index(op.f('ix_analysis_results_owner_id'), 'analysis_results', ['owner_id'], unique=False)
    op.create_table('business_connections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_connection_id', sa.String(length=255), nullable=False),
    sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('user_first_name', sa.String(length=255), nullable=True),
    sa.Column('user_last_name', sa.String(length=255), nullable=True),
    sa.Column('user_username', sa.String(length=255), nullable=True),
    sa.Column('can_reply', sa.Boolean(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('notifications_enabled', sa.Boolean(), nullable=False),
    sa.Column('is_blocked', sa.Boolean(), nullable=False),
    sa.Column('bot_id', sa.BigInteger(), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_business_connections_business_connection_id'), 'business_connections', ['business_connection_id'], unique=True)
    op.create_table('casino_wins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=30), nullable=False),
    sa.Column('game', sa.String(length=10), nullable=False),
    sa.Column('bet', sa.Integer(), nullable=False),
    sa.Column('payout', sa.Integer(), nullable=False),
    sa.Column('net', sa.Integer(), nullable=False),
    sa.Column('mult', sa.Float(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_casino_wins_ts'), 'casino_wins', ['ts'], unique=False)
    op.create_index('ix_casino_wins_ts_net', 'casino_wins', ['ts', 'net'], unique=False)
    op.create_index(op.f('ix_casino_wins_uid'), 'casino_wins', ['uid'], unique=False)
    op.create_table('chat_pets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('pet_name', sa.String(length=50), nullable=False),
    sa.Column('species', sa.String(length=20), nullable=False),
    sa.Column('interlocutor_name', sa.String(length=100), nullable=False),
    sa.Column('is_alive', sa.Boolean(), nullable=False),
    sa.Column('born_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_fed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('died_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('death_cause', sa.String(length=20), nullable=True),
    sa.Column('mood', sa.Integer(), nullable=False),
    sa.Column('xp', sa.Integer(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('personality', sa.String(length=20), nullable=False),
    sa.Column('last_played_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_cuddled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('total_feedings', sa.Integer(), nullable=False),
    sa.Column('total_plays', sa.Integer(), nullable=False),
    sa.Column('total_cuddles', sa.Integer(), nullable=False),
    sa.Column('feed_streak', sa.Integer(), nullable=False),
    sa.Column('upgrades', sa.String(length=400), nullable=True),
    sa.Column('user_a_id', sa.BigInteger(), nullable=False),
    sa.Column('user_b_id', sa.BigInteger(), nullable=False),
    sa.Column('partner_name', sa.String(length=100), nullable=False),
    sa.Column('revival_count', sa.Integer(), nullable=False),
    sa.Column('max_revivals', sa.Integer(), nullable=False),
    sa.Column('last_fed_by', sa.BigInteger(), nullable=True),
    sa.Column('last_played_by', sa.BigInteger(), nullable=True),
    sa.Column('last_cuddled_by', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_pets_owner_telegram_id'), 'chat_pets', ['owner_telegram_id'], unique=False)
    op.create_index('uq_chat_pets_alive', 'chat_pets', ['owner_telegram_id', 'chat_id'], unique=True, postgresql_where=sa.text('is_alive = true'))
    op.create_index('uq_chat_pets_pair_alive', 'chat_pets', ['user_a_id', 'user_b_id'], unique=True, postgresql_where=sa.text('is_alive = true'))
    op.create_table('chat_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_connection_id', sa.String(length=255), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('muted_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delete_msgs_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('business_connection_id', 'chat_id', name='uq_chat_settings')
    )
    op.create_index(op.f('ix_chat_settings_business_connection_id'), 'chat_settings', ['business_connection_id'], unique=False)
    op.create_table('contact_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_connection_id', sa.String(length=255), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contact_notes_business_connection_id'), 'contact_notes', ['business_connection_id'], unique=False)
    op.create_index(op.f('ix_contact_notes_chat_id'), 'contact_notes', ['chat_id'], unique=False)
    op.create_table('daily_quest_completions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('quest_id', sa.String(length=32), nullable=False),
    sa.Column('quest_date', sa.Date(), nullable=False),
    sa.Column('reward', sa.Integer(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_telegram_id', 'quest_id', 'quest_date', name='uq_daily_quest_completion')
    )
    op.create_index(op.f('ix_daily_quest_completions_owner_telegram_id'), 'daily_quest_completions', ['owner_telegram_id'], unique=False)
    op.create_table('giveaway_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_visible_to_all', sa.Boolean(), nullable=False),
    sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
    sa.Column('opens_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('prize_1', sa.String(length=200), nullable=True),
    sa.Column('prize_2', sa.String(length=200), nullable=True),
    sa.Column('prize_3', sa.String(length=200), nullable=True),
    sa.Column('prize_1_image', sa.String(length=512), nullable=True),
    sa.Column('prize_2_image', sa.String(length=512), nullable=True),
    sa.Column('prize_3_image', sa.String(length=512), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('note_reminders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('note_text', sa.Text(), nullable=False),
    sa.Column('event_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('remind_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('advance_minutes', sa.Integer(), nullable=False),
    sa.Column('is_sent', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_note_reminders_owner_telegram_id'), 'note_reminders', ['owner_telegram_id'], unique=False)
    op.create_index(op.f('ix_note_reminders_remind_at'), 'note_reminders', ['remind_at'], unique=False)
    op.create_table('referral_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('referrer_reward_days', sa.Integer(), nullable=False),
    sa.Column('referee_reward_days', sa.Integer(), nullable=False),
    sa.Column('min_account_age_days', sa.Integer(), nullable=False),
    sa.Column('max_referrals_per_day', sa.Integer(), nullable=False),
    sa.Column('milestones', sa.JSON(), nullable=False),
    sa.Column('levels', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('referrals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('referrer_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('referred_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('referred_first_name', sa.String(length=128), nullable=True),
    sa.Column('referred_username', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('fraud_reason', sa.String(length=255), nullable=True),
    sa.Column('milestone_checked', sa.Boolean(), nullable=False),
    sa.Column('evaluation_failures', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_referrals_referred_telegram_id'), 'referrals', ['referred_telegram_id'], unique=True)
    op.create_index(op.f('ix_referrals_referrer_telegram_id'), 'referrals', ['referrer_telegram_id'], unique=False)
    op.create_index(op.f('ix_referrals_status'), 'referrals', ['status'], unique=False)
    op.create_table('relationships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_a_id', sa.BigInteger(), nullable=False),
    sa.Column('user_b_id', sa.BigInteger(), nullable=False),
    sa.Column('initiator_id', sa.BigInteger(), nullable=False),
    sa.Column('rel_type', sa.String(length=20), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('xp', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_gift_a', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_gift_b', sa.DateTime(timezone=True), nullable=True),
    sa.Column('meta', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_a_id', 'user_b_id', name='uq_relationship_pair')
    )
    op.create_index(op.f('ix_relationships_rel_type'), 'relationships', ['rel_type'], unique=False)
    op.create_index(op.f('ix_relationships_status'), 'relationships', ['status'], unique=False)
    op.create_index(op.f('ix_relationships_user_a_id'), 'relationships', ['user_a_id'], unique=False)
    op.create_index(op.f('ix_relationships_user_b_id'), 'relationships', ['user_b_id'], unique=False)
    op.create_table('required_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_username', sa.String(length=128), nullable=False),
    sa.Column('channel_title', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel_username')
    )
    op.create_table('shop_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('items', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('subscription_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('price_stars', sa.Integer(), nullable=False),
    sa.Column('duration_days', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('benefits', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('telegram_updates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bot_id', sa.BigInteger(), nullable=False),
    sa.Column('update_id', sa.BigInteger(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bot_id', 'update_id', name='uq_telegram_updates_bot_update')
    )
    op.create_index('ix_telegram_updates_state_ready', 'telegram_updates', ['state', 'ready_at'], unique=False)
    op.create_table('telegram_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('first_name', sa.String(length=255), nullable=True),
    sa.Column('last_name', sa.String(length=255), nullable=True),
    sa.Column('is_bot', sa.Boolean(), nullable=False),
    sa.Column('photo_file_id', sa.Text(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_telegram_users_telegram_user_id'), 'telegram_users', ['telegram_user_id'], unique=True)
    op.create_table('user_boosts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('boost_type', sa.String(length=50), nullable=False),
    sa.Column('purchased_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_boosts_owner_telegram_id'), 'user_boosts', ['owner_telegram_id'], unique=False)
    op.create_table('user_settings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('theme', sa.String(length=50), server_default='default', nullable=False),
    sa.Column('frame', sa.String(length=50), server_default='none', nullable=False),
    sa.Column('pinned_chat_id', sa.BigInteger(), nullable=True),
    sa.Column('owned_themes', sa.JSON(), nullable=True),
    sa.Column('muted_streaks', sa.JSON(), nullable=True),
    sa.Column('streak_reminders_enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('dl_contact_videos', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('dl_contact_videos_mutual', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('chat_filter_mode', sa.String(length=20), server_default='all', nullable=False),
    sa.Column('chat_whitelist', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_telegram_id')
    )
    op.create_table('user_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('sub_type', sa.String(length=20), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('granted_by_admin', sa.Boolean(), nullable=False),
    sa.Column('payment_charge_id', sa.String(length=255), nullable=True),
    sa.Column('stars_paid', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_subscriptions_user_telegram_id'), 'user_subscriptions', ['user_telegram_id'], unique=False)
    op.create_index('uix_user_subscriptions_charge_id', 'user_subscriptions', ['payment_charge_id'], unique=True, postgresql_where=sa.text('payment_charge_id IS NOT NULL'))
    op.create_index('uix_user_subscriptions_one_active', 'user_subscriptions', ['user_telegram_id'], unique=True, postgresql_where=sa.text('is_active = true'))
    op.create_table('user_wallets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('balance', sa.Integer(), nullable=False),
    sa.Column('total_earned', sa.Integer(), nullable=False),
    sa.Column('total_spent', sa.Integer(), nullable=False),
    sa.Column('last_daily_claim', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_wallets_owner_telegram_id'), 'user_wallets', ['owner_telegram_id'], unique=True)
    op.create_table('vip_subscription_config',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('price_stars', sa.Integer(), nullable=False),
    sa.Column('duration_days', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('benefits', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_connection_id', sa.String(length=255), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('reply_to_message_id', sa.Integer(), nullable=True),
    sa.Column('sender_id', sa.Integer(), nullable=True),
    sa.Column('sender_telegram_id', sa.BigInteger(), nullable=True),
    sa.Column('sender_username', sa.String(length=255), nullable=True),
    sa.Column('sender_first_name', sa.String(length=255), nullable=True),
    sa.Column('sender_last_name', sa.String(length=255), nullable=True),
    sa.Column('is_outgoing', sa.Boolean(), nullable=False),
    sa.Column('is_placeholder', sa.Boolean(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('original_text', sa.Text(), nullable=True),
    sa.Column('original_caption', sa.Text(), nullable=True),
    sa.Column('original_file_id', sa.String(length=512), nullable=True),
    sa.Column('original_file_unique_id', sa.String(length=255), nullable=True),
    sa.Column('original_media_type', sa.String(length=32), nullable=True),
    sa.Column('media_type', sa.Enum('NONE', 'TEXT', 'PHOTO', 'VIDEO', 'VOICE', 'AUDIO', 'DOCUMENT', 'STICKER', 'ANIMATION', 'VIDEO_NOTE', 'CONTACT', 'LOCATION', 'POLL', 'OTHER', name='mediatype', native_enum=False, length=32), nullable=False),
    sa.Column('file_id', sa.String(length=512), nullable=True),
    sa.Column('file_unique_id', sa.String(length=255), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_edited', sa.Boolean(), nullable=False),
    sa.Column('last_edit_update_id', sa.BigInteger(), nullable=True),
    sa.Column('last_edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('edit_count', sa.Integer(), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['sender_id'], ['telegram_users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('business_connection_id', 'chat_id', 'message_id', name='uq_messages_telegram_identity')
    )
    op.create_index(op.f('ix_messages_business_connection_id'), 'messages', ['business_connection_id'], unique=False)
    op.create_index('ix_messages_caption_trgm', 'messages', ['caption'], unique=False, postgresql_using='gin', postgresql_ops={'caption': 'gin_trgm_ops'})
    op.create_index(op.f('ix_messages_chat_id'), 'messages', ['chat_id'], unique=False)
    op.create_index('ix_messages_chat_sent', 'messages', ['chat_id', 'sent_at'], unique=False)
    op.create_index('ix_messages_conn_sent', 'messages', ['business_connection_id', 'sent_at'], unique=False)
    op.create_index(op.f('ix_messages_is_deleted'), 'messages', ['is_deleted'], unique=False)
    op.create_index('ix_messages_search_text', 'messages', ['text'], unique=False)
    op.create_index(op.f('ix_messages_sender_id'), 'messages', ['sender_id'], unique=False)
    op.create_index('ix_messages_text_trgm', 'messages', ['text'], unique=False, postgresql_using='gin', postgresql_ops={'text': 'gin_trgm_ops'})
    op.create_table('referral_reward_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('referral_id', sa.Integer(), nullable=True),
    sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('reward_type', sa.String(length=30), nullable=False),
    sa.Column('reward_value', sa.String(length=100), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['referral_id'], ['referrals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_referral_reward_log_referral_id'), 'referral_reward_log', ['referral_id'], unique=False)
    op.create_index(op.f('ix_referral_reward_log_user_telegram_id'), 'referral_reward_log', ['user_telegram_id'], unique=False)
    op.create_index('uq_milestone_reward_per_user', 'referral_reward_log', ['user_telegram_id', 'reward_value'], unique=True, sqlite_where=sa.text("reward_type = 'milestone'"), postgresql_where=sa.text("reward_type = 'milestone'"))
    op.create_table('message_edit_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('file_id', sa.String(length=512), nullable=True),
    sa.Column('media_type', sa.String(length=32), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_message_edit_history_message_id'), 'message_edit_history', ['message_id'], unique=False)


def downgrade() -> None:
    raise RuntimeError("Clean baseline downgrade requires an approved data-preservation plan")

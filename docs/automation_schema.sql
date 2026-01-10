-- Event-driven action automation & notification system

CREATE TABLE IF NOT EXISTS auto_event_types (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code VARCHAR(120) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT DEFAULT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  source_table VARCHAR(255) DEFAULT NULL,
  source_query LONGTEXT DEFAULT NULL,
  cursor_type VARCHAR(20) NOT NULL DEFAULT 'id',
  cursor_value VARCHAR(255) DEFAULT NULL,
  filters JSON DEFAULT NULL,
  payload_columns JSON DEFAULT NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_action_types (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code VARCHAR(120) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT DEFAULT NULL,
  action_type VARCHAR(120) NOT NULL,
  config JSON DEFAULT NULL,
  action_overrides JSON DEFAULT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_message_templates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code VARCHAR(120) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT DEFAULT NULL,
  subject JSON DEFAULT NULL,
  body JSON DEFAULT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_rules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  description TEXT DEFAULT NULL,
  event_type_id BIGINT UNSIGNED NOT NULL,
  condition JSON DEFAULT NULL,
  action_id BIGINT UNSIGNED DEFAULT NULL,
  message_id BIGINT UNSIGNED DEFAULT NULL,
  channels JSON DEFAULT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY auto_rules_event_type_idx (event_type_id),
  KEY auto_rules_action_idx (action_id),
  KEY auto_rules_message_idx (message_id),
  CONSTRAINT auto_rules_event_type_fk FOREIGN KEY (event_type_id) REFERENCES auto_event_types (id) ON DELETE CASCADE,
  CONSTRAINT auto_rules_action_fk FOREIGN KEY (action_id) REFERENCES auto_action_types (id) ON DELETE SET NULL,
  CONSTRAINT auto_rules_message_fk FOREIGN KEY (message_id) REFERENCES auto_message_templates (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_event_tampon (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_type_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  source_table VARCHAR(255) DEFAULT NULL,
  source_pk VARCHAR(255) DEFAULT NULL,
  source VARCHAR(60) DEFAULT 'poller',
  payload JSON DEFAULT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'pending',
  processed_at TIMESTAMP NULL DEFAULT NULL,
  error TEXT DEFAULT NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY auto_event_tampon_status_idx (status),
  KEY auto_event_tampon_event_idx (event_type_id),
  CONSTRAINT auto_event_tampon_event_fk FOREIGN KEY (event_type_id) REFERENCES auto_event_types (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_action_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  action_id BIGINT UNSIGNED NOT NULL,
  rule_id BIGINT UNSIGNED NOT NULL,
  event_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  payload JSON DEFAULT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'pending',
  attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  last_error TEXT DEFAULT NULL,
  processed_at TIMESTAMP NULL DEFAULT NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY auto_action_queue_status_idx (status),
  KEY auto_action_queue_event_idx (event_id),
  CONSTRAINT auto_action_queue_action_fk FOREIGN KEY (action_id) REFERENCES auto_action_types (id) ON DELETE CASCADE,
  CONSTRAINT auto_action_queue_rule_fk FOREIGN KEY (rule_id) REFERENCES auto_rules (id) ON DELETE CASCADE,
  CONSTRAINT auto_action_queue_event_fk FOREIGN KEY (event_id) REFERENCES auto_event_tampon (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_notification_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  message_id BIGINT UNSIGNED NOT NULL,
  rule_id BIGINT UNSIGNED NOT NULL,
  event_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  payload JSON DEFAULT NULL,
  channels JSON DEFAULT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'pending',
  attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  last_error TEXT DEFAULT NULL,
  processed_at TIMESTAMP NULL DEFAULT NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY auto_notification_status_idx (status),
  KEY auto_notification_event_idx (event_id),
  CONSTRAINT auto_notification_message_fk FOREIGN KEY (message_id) REFERENCES auto_message_templates (id) ON DELETE CASCADE,
  CONSTRAINT auto_notification_rule_fk FOREIGN KEY (rule_id) REFERENCES auto_rules (id) ON DELETE CASCADE,
  CONSTRAINT auto_notification_event_fk FOREIGN KEY (event_id) REFERENCES auto_event_tampon (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auto_rule_executions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  rule_id BIGINT UNSIGNED NOT NULL,
  event_id BIGINT UNSIGNED NOT NULL,
  action_queue_id BIGINT UNSIGNED DEFAULT NULL,
  notification_queue_id BIGINT UNSIGNED DEFAULT NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY auto_rule_exec_unique (rule_id, event_id),
  CONSTRAINT auto_rule_exec_rule_fk FOREIGN KEY (rule_id) REFERENCES auto_rules (id) ON DELETE CASCADE,
  CONSTRAINT auto_rule_exec_event_fk FOREIGN KEY (event_id) REFERENCES auto_event_tampon (id) ON DELETE CASCADE,
  CONSTRAINT auto_rule_exec_action_fk FOREIGN KEY (action_queue_id) REFERENCES auto_action_queue (id) ON DELETE SET NULL,
  CONSTRAINT auto_rule_exec_notification_fk FOREIGN KEY (notification_queue_id) REFERENCES auto_notification_queue (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

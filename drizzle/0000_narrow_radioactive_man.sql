CREATE TABLE `approvals` (
	`revision_id` text PRIMARY KEY NOT NULL,
	`digest` text NOT NULL,
	`reviewer` text NOT NULL,
	`note` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `artifacts` (
	`id` text PRIMARY KEY NOT NULL,
	`submission_id` text NOT NULL,
	`name` text NOT NULL,
	`sha256` text NOT NULL,
	`size` integer NOT NULL,
	`object_key` text NOT NULL,
	`format` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `artifacts_submission` ON `artifacts` (`submission_id`);--> statement-breakpoint
CREATE TABLE `audit` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`submission_id` text,
	`action` text NOT NULL,
	`detail` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `audit_submission` ON `audit` (`submission_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `members` (
	`user_id` text NOT NULL,
	`partner_id` text NOT NULL,
	`role` text NOT NULL,
	PRIMARY KEY(`user_id`, `partner_id`)
);
--> statement-breakpoint
CREATE TABLE `operations` (
	`id` text PRIMARY KEY NOT NULL,
	`publication_id` text NOT NULL,
	`name` text NOT NULL,
	`state` text NOT NULL,
	`remote_id` text,
	`evidence` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `operation_unique` ON `operations` (`publication_id`,`name`);--> statement-breakpoint
CREATE TABLE `partners` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `profiles` (
	`id` text PRIMARY KEY NOT NULL,
	`partner_id` text NOT NULL,
	`modality` text NOT NULL,
	`format` text NOT NULL,
	`version` integer NOT NULL,
	`name` text NOT NULL,
	`content` text NOT NULL,
	`digest` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `profile_versions` ON `profiles` (`partner_id`,`modality`,`format`,`name`,`version`);--> statement-breakpoint
CREATE TABLE `publications` (
	`id` text PRIMARY KEY NOT NULL,
	`revision_id` text NOT NULL,
	`tenant` text NOT NULL,
	`destination` text NOT NULL,
	`state` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `publication_unique` ON `publications` (`revision_id`,`tenant`,`destination`);--> statement-breakpoint
CREATE TABLE `revisions` (
	`id` text PRIMARY KEY NOT NULL,
	`submission_id` text NOT NULL,
	`parent_id` text,
	`number` integer NOT NULL,
	`payload_key` text NOT NULL,
	`digest` text NOT NULL,
	`context` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `submission_revision_number` ON `revisions` (`submission_id`,`number`);--> statement-breakpoint
CREATE TABLE `settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `submissions` (
	`id` text PRIMARY KEY NOT NULL,
	`partner_id` text NOT NULL,
	`modality` text NOT NULL,
	`title` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL,
	`latest_revision` text
);
--> statement-breakpoint
CREATE INDEX `submissions_partner_date` ON `submissions` (`partner_id`,`created_at`);
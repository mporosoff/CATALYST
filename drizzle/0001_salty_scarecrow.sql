DROP INDEX `profile_versions`;--> statement-breakpoint
ALTER TABLE `profiles` ADD `source_version` text DEFAULT 'legacy' NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX `profile_versions` ON `profiles` (`partner_id`,`modality`,`format`,`source_version`,`name`,`version`);
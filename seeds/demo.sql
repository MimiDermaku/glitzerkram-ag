BEGIN TRANSACTION;
CREATE TABLE bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('kommt','geht','abwesend','wieder_da','pause','pausenende')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            note TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0, ticket_action TEXT CHECK(ticket_action IN ('aendern','loeschen')), ticket_message TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
INSERT INTO "bookings" VALUES(2,2,'geht','2025-08-29 13:30:25','[Admin chef]',1,'aendern','Bin erst um 16 Uhr gegangen');
INSERT INTO "bookings" VALUES(3,2,'abwesend','2025-08-29 13:30:29',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(4,2,'wieder_da','2025-08-29 13:30:33',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(5,2,'pause','2025-08-29 13:30:35',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(6,2,'pausenende','2025-08-29 13:30:37',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(7,2,'kommt','2025-08-29 13:32:33',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(8,2,'geht','2025-08-29 13:32:39','',0,NULL,NULL);
INSERT INTO "bookings" VALUES(9,2,'geht','2025-08-29 13:43:30',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(10,2,'geht','2025-08-29 13:43:33',NULL,0,'loeschen','rdtr');
INSERT INTO "bookings" VALUES(11,2,'pause','2025-08-29 14:22:36',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(12,2,'kommt','2025-08-29 14:59:03',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(13,2,'abwesend','2025-08-29 14:59:10',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(14,2,'pausenende','2025-08-29 14:59:30',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(15,2,'kommt','2025-08-29 15:11:38',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(16,2,'kommt','2025-08-29 15:57:04',NULL,0,NULL,NULL);
INSERT INTO "bookings" VALUES(17,2,'kommt','2025-08-30 07:14:39','Projekt Glitzerkram',0,NULL,NULL);
CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user'))
        , join_date TEXT, weekly_minutes INTEGER DEFAULT 2400, is_active INTEGER DEFAULT 1);
INSERT INTO "users" VALUES(1,'chef','scrypt:32768:8:1$aPDXxqI5XeYDqeUc$96508232f8d0d483fc63c9ce5eac40ced3e6115710c09ed29dcdebcad61c154f39fa421e978f18cc9528e3919d15cb371d0e34c59cd50c6daa45239a95bde28a','admin',NULL,2400,1);
INSERT INTO "users" VALUES(2,'mimi','scrypt:32768:8:1$ddi9uBvnGsjE6uoZ$f67b0569634cc4eb2a2a0661828946586e55a37b33baebece25abae9f3782cf015baed327eea9bd83cf3647a35a33180f017e1c8ec773fc9aa93ff577e83a325','user',NULL,2100,0);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',2);
INSERT INTO "sqlite_sequence" VALUES('bookings',17);
COMMIT;

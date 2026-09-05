const db = require('./backend/config/database');

async function fixForeignKeys() {
    try {
        console.log('Fixing foreign keys for cameras table...');
        
        // 1. Drop existing FKs (need to know their names. In MySQL, if not named, it's usually table_ibfk_n)
        // A better way is to query information_schema for the constraint names.
        
        const [violationFks] = await db.query(`
            SELECT CONSTRAINT_NAME 
            FROM information_schema.KEY_COLUMN_USAGE 
            WHERE TABLE_SCHEMA = 'construction_safety' 
            AND TABLE_NAME = 'violations' 
            AND REFERENCED_TABLE_NAME = 'cameras'
        `);
        
        for (let fk of violationFks) {
            await db.query(`ALTER TABLE violations DROP FOREIGN KEY ${fk.CONSTRAINT_NAME}`);
            console.log('Dropped FK from violations:', fk.CONSTRAINT_NAME);
        }
        
        await db.query(`ALTER TABLE violations ADD CONSTRAINT fk_violations_camera FOREIGN KEY (camera_id) REFERENCES cameras(camera_id) ON DELETE SET NULL`);

        const [healthFks] = await db.query(`
            SELECT CONSTRAINT_NAME 
            FROM information_schema.KEY_COLUMN_USAGE 
            WHERE TABLE_SCHEMA = 'construction_safety' 
            AND TABLE_NAME = 'health_alerts' 
            AND REFERENCED_TABLE_NAME = 'cameras'
        `);
        
        for (let fk of healthFks) {
            await db.query(`ALTER TABLE health_alerts DROP FOREIGN KEY ${fk.CONSTRAINT_NAME}`);
            console.log('Dropped FK from health_alerts:', fk.CONSTRAINT_NAME);
        }
        
        await db.query(`ALTER TABLE health_alerts ADD CONSTRAINT fk_health_alerts_camera FOREIGN KEY (camera_id) REFERENCES cameras(camera_id) ON DELETE SET NULL`);

        console.log('Successfully fixed foreign keys (ON DELETE SET NULL)!');
    } catch (e) {
        console.error('Error:', e);
    } finally {
        process.exit();
    }
}
fixForeignKeys();

const db = require('./config/database');

async function alterDb() {
    try {
        console.log('Adding camera_source column to cameras table...');
        await db.query(`ALTER TABLE cameras ADD COLUMN camera_source VARCHAR(255) DEFAULT '0'`);
        console.log('Update successful!');
        
        // Update default values for existing cameras
        await db.query(`UPDATE cameras SET camera_source = '0' WHERE camera_id = 1`);
        await db.query(`UPDATE cameras SET camera_source = '1' WHERE camera_id = 2`);
        await db.query(`UPDATE cameras SET camera_source = 'imou' WHERE camera_id = 3`);
        await db.query(`UPDATE cameras SET camera_source = 'imou' WHERE camera_id = 4`);
        console.log('Seed data updated!');
    } catch (e) {
        if (e.code === 'ER_DUP_FIELDNAME') {
            console.log('Column already exists, ignoring.');
        } else {
            console.error('Error:', e);
        }
    } finally {
        process.exit();
    }
}
alterDb();

const db = require('./backend/config/database');

async function checkDatabase() {
    try {
        console.log('Checking recent violations and fines:');
        const [violations] = await db.query('SELECT * FROM violations ORDER BY timestamp DESC LIMIT 5');
        console.log('\n--- Recent Violations ---');
        console.log(violations);

        const [fines] = await db.query('SELECT * FROM fines ORDER BY created_at DESC LIMIT 5');
        console.log('\n--- Recent Fines ---');
        console.log(fines);

        const [salaries] = await db.query('SELECT * FROM salary ORDER BY updated_at DESC LIMIT 5');
        console.log('\n--- Recent Salaries ---');
        console.log(salaries);
    } catch (err) {
        console.error(err);
    } finally {
        process.exit();
    }
}
checkDatabase();

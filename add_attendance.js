const db = require('./backend/config/database');
async function add() {
    try {
        await db.query('DELETE FROM attendance WHERE worker_id = "2"'); // cleanup first
        for(let i=1; i<=20; i++) {
            const date = `2026-07-${i.toString().padStart(2, '0')}`;
            await db.query(
                'INSERT INTO attendance (worker_id, attendance_date, status, check_in_time, check_out_time, working_hours) VALUES (?, ?, ?, ?, ?, ?)',
                ['2', date, 'present', `${date} 09:00:00`, `${date} 17:00:00`, 8]
            );
        }
        
        // Also add attendance for worker 123 so they don't have 0 salary either
        await db.query('DELETE FROM attendance WHERE worker_id = "123"');
        for(let i=1; i<=20; i++) {
            const date = `2026-07-${i.toString().padStart(2, '0')}`;
            await db.query(
                'INSERT INTO attendance (worker_id, attendance_date, status, check_in_time, check_out_time, working_hours) VALUES (?, ?, ?, ?, ?, ?)',
                ['123', date, 'present', `${date} 09:00:00`, `${date} 17:00:00`, 8]
            );
        }
        
        // Recalculate salaries
        const { recalculateSalary } = require('./backend/utils/payroll');
        await recalculateSalary('2', '2026-07');
        await recalculateSalary('123', '2026-07');
        
        console.log('✅ Added 20 days attendance for workers and recalculated salary');
    } catch (e) {
        console.error(e);
    } finally {
        process.exit();
    }
}
add();

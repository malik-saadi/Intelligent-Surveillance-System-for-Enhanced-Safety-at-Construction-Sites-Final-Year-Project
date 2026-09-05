const cron = require('node-cron');
const db = require('../config/database');
const { recalculateSalary } = require('./payroll');

function initCronJobs() {
    // Run every day at 10:00 AM
    cron.schedule('0 10 * * *', async () => {
        console.log('[CRON] Running daily absence check at 10:00 AM...');
        try {
            const today = new Date().toISOString().split('T')[0];
            const payPeriod = today.substring(0, 7);

            // Get all workers
            const [workers] = await db.query('SELECT worker_id FROM workers');

            for (let worker of workers) {
                const workerId = worker.worker_id;

                // Check if worker checked in today
                const [attendance] = await db.query(
                    'SELECT attendance_id FROM attendance WHERE worker_id = ? AND attendance_date = ?',
                    [workerId, today]
                );

                if (attendance.length === 0) {
                    // Worker hasn't checked in -> Mark Absent
                    console.log(`[CRON] Worker ${workerId} is absent today. Marking absent and issuing fine.`);
                    
                    await db.query(
                        'INSERT INTO attendance (worker_id, attendance_date, status) VALUES (?, ?, "absent")',
                        [workerId, today]
                    );

                    // Issue 500 PKR Absence Fine
                    await db.query(
                        `INSERT INTO fines (worker_id, fine_type, fine_amount, description, fine_date, status) 
                         VALUES (?, 'absence', 500, 'Automatic absence fine (Not checked in by 10:00 AM)', ?, 'pending')`,
                        [workerId, today]
                    );

                    // Recalculate salary
                    await recalculateSalary(workerId, payPeriod);
                }
            }
            console.log('[CRON] Daily absence check completed.');
        } catch (error) {
            console.error('[CRON] Error running daily absence check:', error);
        }
    });

    // Run every day at 5:00 PM (17:00) for auto-checkout
    cron.schedule('0 17 * * *', async () => {
        console.log('[CRON] Running auto-checkout at 5:00 PM...');
        try {
            const today = new Date().toISOString().split('T')[0];
            const payPeriod = today.substring(0, 7);
            
            // Get all workers who checked in today but haven't checked out
            const [activeAttendance] = await db.query(
                'SELECT attendance_id, worker_id, check_in_time FROM attendance WHERE DATE(check_in_time) = ? AND check_out_time IS NULL',
                [today]
            );

            const checkOutTime = new Date();
            checkOutTime.setHours(17, 0, 0, 0); // Force to exactly 5:00 PM

            for (let record of activeAttendance) {
                // Calculate hours between check-in and 5:00 PM
                const checkInDate = new Date(record.check_in_time);
                const hoursWorked = (checkOutTime - checkInDate) / (1000 * 60 * 60);

                await db.query(
                    'UPDATE attendance SET check_out_time = ?, working_hours = ? WHERE attendance_id = ?',
                    [checkOutTime, parseFloat(hoursWorked.toFixed(2)), record.attendance_id]
                );
                
                // Recalculate salary to include today's hours
                await recalculateSalary(record.worker_id, payPeriod);
                console.log(`[CRON] Auto-checked out worker ${record.worker_id}. Hours logged: ${hoursWorked.toFixed(2)}`);
            }
            console.log('[CRON] Auto-checkout completed.');
        } catch (error) {
            console.error('[CRON] Error running auto-checkout:', error);
        }
    });
}

module.exports = { initCronJobs };

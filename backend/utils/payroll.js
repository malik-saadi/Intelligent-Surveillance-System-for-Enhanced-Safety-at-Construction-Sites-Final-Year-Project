const db = require('../config/database');

/**
 * Calculates and updates the salary record for a worker for a specific pay period (YYYY-MM).
 * @param {string} workerId 
 * @param {string} payPeriod YYYY-MM
 */
async function recalculateSalary(workerId, payPeriod) {
    try {
        // 1. Fetch worker info
        const [workers] = await db.query(
            'SELECT wage_type, wage_rate FROM workers WHERE worker_id = ?',
            [workerId]
        );
        if (workers.length === 0) {
            console.error(`[Payroll Error] Worker ${workerId} not found.`);
            return;
        }
        const { wage_type, wage_rate } = workers[0];
        const rate = parseFloat(wage_rate);

        // 2. Calculate attendance metrics
        // Count days present/late/leave as days worked
        const [attendanceDays] = await db.query(
            `SELECT COUNT(*) as days 
             FROM attendance 
             WHERE worker_id = ? 
             AND DATE_FORMAT(attendance_date, '%Y-%m') = ? 
             AND status IN ('present', 'late', 'leave')`,
            [workerId, payPeriod]
        );
        const daysWorked = attendanceDays[0].days || 0;

        // Sum working hours
        const [attendanceHours] = await db.query(
            `SELECT SUM(working_hours) as hours 
             FROM attendance 
             WHERE worker_id = ? 
             AND DATE_FORMAT(attendance_date, '%Y-%m') = ?`,
            [workerId, payPeriod]
        );
        const hoursWorked = parseFloat(attendanceHours[0].hours) || 0;

        // 3. Compute Gross Salary
        let grossSalary = 0;
        if (wage_type === 'daily') {
            grossSalary = daysWorked * rate;
        } else if (wage_type === 'hourly') {
            grossSalary = hoursWorked * rate;
        }

        // 4. Retrieve and aggregate non-waived fines
        const [fines] = await db.query(
            `SELECT fine_type, SUM(fine_amount) as total_amount 
             FROM fines 
             WHERE worker_id = ? 
             AND DATE_FORMAT(fine_date, '%Y-%m') = ? 
             AND status != 'waived'
             GROUP BY fine_type`,
            [workerId, payPeriod]
        );

        let violationFines = 0;
        let absenceFines = 0;
        let lateFines = 0;
        let otherFines = 0;

        fines.forEach(f => {
            const amt = parseFloat(f.total_amount) || 0;
            if (f.fine_type === 'violation') violationFines = amt;
            else if (f.fine_type === 'absence') absenceFines = amt;
            else if (f.fine_type === 'late') lateFines = amt;
            else otherFines = amt;
        });

        const totalFines = violationFines + absenceFines + lateFines + otherFines;

        // 5. Calculate Tax (Flat 5%)
        const taxDeduction = parseFloat((grossSalary * 0.05).toFixed(2));

        // 6. Compute Net Salary
        let netSalary = grossSalary - totalFines - taxDeduction;
        if (netSalary < 0) netSalary = 0;
        netSalary = parseFloat(netSalary.toFixed(2));

        // 7. Upsert salary record
        await db.query(
            `INSERT INTO salary (
                worker_id, pay_period, days_worked, hours_worked, rate_per_day_hour, gross_salary,
                violation_fines, absence_fines, late_fines, other_fines, total_fines,
                tax_deduction, other_deduction, net_salary, status
             ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, 0, ?, 'pending'
             ) ON DUPLICATE KEY UPDATE
                days_worked = VALUES(days_worked),
                hours_worked = VALUES(hours_worked),
                rate_per_day_hour = VALUES(rate_per_day_hour),
                gross_salary = VALUES(gross_salary),
                violation_fines = VALUES(violation_fines),
                absence_fines = VALUES(absence_fines),
                late_fines = VALUES(late_fines),
                other_fines = VALUES(other_fines),
                total_fines = VALUES(total_fines),
                tax_deduction = VALUES(tax_deduction),
                net_salary = VALUES(net_salary)`,
            [
                workerId, payPeriod, daysWorked, hoursWorked, rate, grossSalary,
                violationFines, absenceFines, lateFines, otherFines, totalFines,
                taxDeduction, netSalary
            ]
        );

        console.log(`[Payroll Success] Recalculated salary for ${workerId} for period ${payPeriod}. Net: PKR ${netSalary}`);
    } catch (error) {
        console.error(`[Payroll Error] Failed to calculate salary for ${workerId}:`, error);
        throw error;
    }
}

module.exports = {
    recalculateSalary
};

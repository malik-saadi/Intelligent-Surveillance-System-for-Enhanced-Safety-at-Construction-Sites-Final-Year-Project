const express = require('express');
const router = express.Router();
const db = require('../config/database');
const { recalculateSalary } = require('../utils/payroll');

// Get salary records
router.get('/', async (req, res) => {
    try {
        const { pay_period, worker_id, status } = req.query;
        
        let query = `
            SELECT 
                w.worker_id, w.name, w.wage_type, w.wage_rate,
                IFNULL(s.salary_id, 'new') as salary_id,
                IFNULL(s.pay_period, ?) as pay_period,
                IFNULL(s.days_worked, 0) as days_worked,
                IFNULL(s.hours_worked, 0) as hours_worked,
                IFNULL(s.gross_salary, 0) as gross_salary,
                IFNULL(s.violation_fines, 0) as violation_fines,
                IFNULL(s.absence_fines, 0) as absence_fines,
                IFNULL(s.late_fines, 0) as late_fines,
                IFNULL(s.total_fines, 0) as total_fines,
                IFNULL(s.tax_deduction, 0) as tax_deduction,
                IFNULL(s.net_salary, 0) as net_salary,
                IFNULL(s.status, 'pending') as status
            FROM workers w
            LEFT JOIN salary s ON w.worker_id = s.worker_id AND s.pay_period = ?
            WHERE w.status = 'active'
        `;
        const params = [pay_period, pay_period];
        
        if (worker_id) {
            query += ' AND w.worker_id = ?';
            params.push(worker_id);
        }
        if (status) {
            // Note: Since we are filling 'pending' for empty rows, this filter needs to handle IFNULL
            query += " AND IFNULL(s.status, 'pending') = ?";
            params.push(status);
        }
        
        query += ' ORDER BY w.worker_id ASC';
        
        const [rows] = await db.query(query, params);
        res.json(rows);
    } catch (error) {
        console.error('Error fetching salary records:', error);
        res.status(500).json({ error: error.message });
    }
});

// Get salary summary
router.get('/stats/summary', async (req, res) => {
    try {
        const { pay_period } = req.query;
        const period = pay_period || new Date().toISOString().slice(0, 7); // YYYY-MM
        
        const [summary] = await db.query(`
            SELECT 
                SUM(gross_salary) as total_gross,
                SUM(total_fines) as total_fines,
                SUM(net_salary) as total_net,
                AVG(net_salary) as avg_salary,
                COUNT(*) as worker_count
            FROM salary
            WHERE pay_period = ?
        `, [period]);
        
        res.json(summary[0]);
    } catch (error) {
        console.error('Error fetching salary summary:', error);
        res.status(500).json({ error: error.message });
    }
});

// Get salary detail for a worker
router.get('/worker/:worker_id', async (req, res) => {
    try {
        const { pay_period } = req.query;
        
        const [salary] = await db.query(`
            SELECT s.*, w.name, w.wage_type, w.wage_rate
            FROM salary s
            JOIN workers w ON s.worker_id = w.worker_id
            WHERE s.worker_id = ? AND s.pay_period = ?
        `, [req.params.worker_id, pay_period]);
        
        let salaryRecord = salary[0] || null;
        
        // Dynamically compute and create the salary if it doesn't exist yet
        if (!salaryRecord) {
            try {
                await recalculateSalary(req.params.worker_id, pay_period);
                const [retrySalary] = await db.query(`
                    SELECT s.*, w.name, w.wage_type, w.wage_rate
                    FROM salary s
                    JOIN workers w ON s.worker_id = w.worker_id
                    WHERE s.worker_id = ? AND s.pay_period = ?
                `, [req.params.worker_id, pay_period]);
                salaryRecord = retrySalary[0] || null;
            } catch (calcError) {
                console.error('Failed to dynamic calc:', calcError);
            }
        }
        
        // Get fines for this worker and pay period
        const [fines] = await db.query(`
            SELECT fine_id, fine_date, fine_type, fine_amount, description, status
            FROM fines
            WHERE worker_id = ? 
            AND DATE_FORMAT(fine_date, '%Y-%m') = ?
            ORDER BY fine_date DESC
        `, [req.params.worker_id, pay_period]);
        
        res.json({
            salary: salaryRecord,
            fines: fines
        });
    } catch (error) {
        console.error('Error fetching worker salary:', error);
        res.status(500).json({ error: error.message });
    }
});

// Create or update salary record
router.post('/', async (req, res) => {
    try {
        const { worker_id, pay_period, days_worked, hours_worked, gross_salary, total_fines, net_salary } = req.body;
        
        const [result] = await db.query(
            `INSERT INTO salary (worker_id, pay_period, days_worked, hours_worked, gross_salary, total_fines, net_salary)
             VALUES (?, ?, ?, ?, ?, ?, ?)
             ON DUPLICATE KEY UPDATE
             days_worked = VALUES(days_worked),
             hours_worked = VALUES(hours_worked),
             gross_salary = VALUES(gross_salary),
             total_fines = VALUES(total_fines),
             net_salary = VALUES(net_salary)`,
            [worker_id, pay_period, days_worked, hours_worked, gross_salary, total_fines, net_salary]
        );
        
        res.status(201).json({ message: 'Salary record saved successfully' });
    } catch (error) {
        console.error('Error saving salary:', error);
        res.status(500).json({ error: error.message });
    }
});

// Update salary status (for payment processing)
router.put('/:salary_id/status', async (req, res) => {
    try {
        const { status } = req.body;
        const payment_date = status === 'paid' ? new Date() : null;
        
        const [result] = await db.query(
            'UPDATE salary SET status = ?, payment_date = ? WHERE salary_id = ?',
            [status, payment_date, req.params.salary_id]
        );
        
        if (result.affectedRows === 0) {
            return res.status(404).json({ error: 'Salary record not found' });
        }
        
        res.json({ message: 'Salary status updated successfully' });
    } catch (error) {
        console.error('Error updating salary status:', error);
        res.status(500).json({ error: error.message });
    }
});

// Process payroll (Generates and updates salary records for all active workers)
router.post('/process-payroll', async (req, res) => {
    try {
        const { pay_period } = req.body;
        if (!pay_period) {
            return res.status(400).json({ error: 'pay_period is required (YYYY-MM)' });
        }

        // Fetch all active workers
        const [workers] = await db.query("SELECT worker_id FROM workers WHERE status = 'active'");
        
        for (let worker of workers) {
            await recalculateSalary(worker.worker_id, pay_period);
        }

        res.json({ 
            success: true,
            message: `Payroll processed successfully for ${workers.length} active workers.`,
            affected_records: workers.length
        });
    } catch (error) {
        console.error('Error processing payroll:', error);
        res.status(500).json({ error: error.message });
    }
});

// Make a manual adjustment (Bonus / Fine)
router.post('/adjustment', async (req, res) => {
    try {
        const { worker_id, amount, description, type, pay_period } = req.body;
        const date = new Date().toISOString().split('T')[0];
        
        // Amount is sent as positive for both. If it's a bonus, we store it as a negative fine.
        const fineAmount = type === 'bonus' ? -Math.abs(amount) : Math.abs(amount);
        
        await db.query(`
            INSERT INTO fines (worker_id, fine_type, fine_amount, description, fine_date, status)
            VALUES (?, 'other', ?, ?, ?, 'pending')
        `, [worker_id, fineAmount, description || `Manual ${type}`, date]);
        
        // Recalculate
        await recalculateSalary(worker_id, pay_period);
        
        res.json({ message: 'Adjustment applied successfully' });
    } catch (error) {
        console.error('Error applying adjustment:', error);
        res.status(500).json({ error: error.message });
    }
});

// Reset Salaries
router.post('/reset', async (req, res) => {
    try {
        const { pay_period } = req.body;
        if (!pay_period) return res.status(400).json({ error: 'pay_period is required' });

        await db.query('DELETE FROM fines WHERE DATE_FORMAT(fine_date, "%Y-%m") = ?', [pay_period]);
        await db.query('DELETE FROM salary WHERE pay_period = ?', [pay_period]);
        
        res.json({ message: 'All salaries and fines reset successfully for ' + pay_period });
    } catch (error) {
        console.error('Error resetting salaries:', error);
        res.status(500).json({ error: error.message });
    }
});

module.exports = router;
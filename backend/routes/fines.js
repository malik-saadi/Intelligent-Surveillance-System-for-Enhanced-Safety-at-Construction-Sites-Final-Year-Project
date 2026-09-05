const express = require('express');
const router = express.Router();
const db = require('../config/database');
const { recalculateSalary } = require('../utils/payroll');

// Get all fines with filters
router.get('/', async (req, res) => {
    try {
        const { fine_type, status, pay_period } = req.query;
        
        let query = `
            SELECT f.*, w.name 
            FROM fines f
            JOIN workers w ON f.worker_id = w.worker_id
            WHERE 1=1
        `;
        const params = [];
        
        if (fine_type) {
            query += ' AND f.fine_type = ?';
            params.push(fine_type);
        }
        if (status) {
            query += ' AND f.status = ?';
            params.push(status);
        }
        if (pay_period) {
            query += " AND DATE_FORMAT(f.fine_date, '%Y-%m') = ?";
            params.push(pay_period);
        }
        
        query += ' ORDER BY f.fine_date DESC, f.fine_id DESC';
        
        const [rows] = await db.query(query, params);
        res.json(rows);
    } catch (error) {
        console.error('Error fetching fines:', error);
        res.status(500).json({ error: error.message });
    }
});

// Update fine status (deduct or waive)
router.put('/:id/status', async (req, res) => {
    try {
        const { status } = req.body;
        const fineId = req.params.id;
        
        if (!['pending', 'deducted', 'waived'].includes(status)) {
            return res.status(400).json({ error: 'Invalid status value' });
        }
        
        // 1. Get fine details first to retrieve worker_id and fine_date
        const [fines] = await db.query('SELECT worker_id, fine_date FROM fines WHERE fine_id = ?', [fineId]);
        if (fines.length === 0) {
            return res.status(404).json({ error: 'Fine not found' });
        }
        const { worker_id, fine_date } = fines[0];
        const payPeriod = new Date(fine_date).toISOString().slice(0, 7); // YYYY-MM
        
        // 2. Update status
        await db.query('UPDATE fines SET status = ? WHERE fine_id = ?', [status, fineId]);
        
        // 3. Recalculate salary
        await recalculateSalary(worker_id, payPeriod);
        
        res.json({ success: true, message: `Fine status updated to ${status} and salary recalculated.` });
    } catch (error) {
        console.error('Error updating fine status:', error);
        res.status(500).json({ error: error.message });
    }
});

module.exports = router;

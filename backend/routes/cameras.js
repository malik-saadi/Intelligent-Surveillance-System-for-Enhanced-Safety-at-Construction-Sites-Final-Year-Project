const express = require('express');
const router = express.Router();
const db = require('../config/database');

// GET all cameras
router.get('/', async (req, res) => {
    try {
        const [cameras] = await db.query('SELECT * FROM cameras');
        res.json({ success: true, cameras });
    } catch (error) {
        console.error('Error fetching cameras:', error);
        res.status(500).json({ success: false, message: 'Server error fetching cameras' });
    }
});

// POST a new camera
router.post('/', async (req, res) => {
    try {
        const { camera_name, location, camera_source, resolution, fps } = req.body;
        
        if (!camera_name || !location || !camera_source) {
            return res.status(400).json({ success: false, message: 'Please provide camera name, location, and source.' });
        }

        const [result] = await db.query(
            'INSERT INTO cameras (camera_name, location, camera_source, resolution, fps) VALUES (?, ?, ?, ?, ?)',
            [camera_name, location, camera_source, resolution || '1920x1080', fps || 30]
        );

        res.json({ success: true, message: 'Camera added successfully', camera_id: result.insertId });
    } catch (error) {
        console.error('Error adding camera:', error);
        res.status(500).json({ success: false, message: 'Server error adding camera' });
    }
});

// DELETE a camera
router.delete('/:id', async (req, res) => {
    try {
        const cameraId = req.params.id;
        
        const [result] = await db.query('DELETE FROM cameras WHERE camera_id = ?', [cameraId]);
        
        if (result.affectedRows === 0) {
             return res.status(404).json({ success: false, message: 'Camera not found.' });
        }

        res.json({ success: true, message: 'Camera deleted successfully' });
    } catch (error) {
        console.error('Error deleting camera:', error);
        res.status(500).json({ success: false, message: 'Server error deleting camera' });
    }
});

// UPDATE a camera
router.put('/:id', async (req, res) => {
    try {
        const cameraId = req.params.id;
        const { camera_source } = req.body;
        
        if (!camera_source) {
            return res.status(400).json({ success: false, message: 'Please provide camera source.' });
        }

        const [result] = await db.query(
            'UPDATE cameras SET camera_source = ? WHERE camera_id = ?',
            [camera_source, cameraId]
        );

        if (result.affectedRows === 0) {
             return res.status(404).json({ success: false, message: 'Camera not found.' });
        }

        res.json({ success: true, message: 'Camera updated successfully' });
    } catch (error) {
        console.error('Error updating camera:', error);
        res.status(500).json({ success: false, message: 'Server error updating camera' });
    }
});

module.exports = router;

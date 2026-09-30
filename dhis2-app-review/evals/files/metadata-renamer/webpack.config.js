require('dotenv').config()
const HtmlWebpackPlugin = require('html-webpack-plugin')

const target = process.env.DHIS2_BASE_URL
const auth = Buffer.from(
    `${process.env.DHIS2_USERNAME}:${process.env.DHIS2_PASSWORD}`
).toString('base64')

module.exports = {
    entry: './src/index.js',
    output: { filename: 'app.js', clean: true },
    plugins: [new HtmlWebpackPlugin({ template: './src/index.html' })],
    devServer: {
        port: Number(process.env.DHIS2_DEV_PORT || 8081),
        proxy: [
            {
                context: ['/api'],
                target,
                changeOrigin: true,
                headers: { Authorization: `Basic ${auth}` },
            },
        ],
    },
}

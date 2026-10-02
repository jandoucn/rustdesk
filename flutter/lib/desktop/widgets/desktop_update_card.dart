import 'package:flutter/material.dart';

class DesktopUpdateCard extends StatelessWidget {
  final String version;
  final String installLabel;
  final VoidCallback onInstall;
  final VoidCallback onClose;

  const DesktopUpdateCard({
    super.key,
    required this.version,
    required this.installLabel,
    required this.onInstall,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 20),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          colors: [Color(0xFFE242BC), Color(0xFFF4727C)],
        ),
      ),
      child: Stack(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 36, 20, 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  version,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 20),
                Center(
                  child: OutlinedButton(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      side: const BorderSide(color: Colors.white),
                      minimumSize: const Size(150, 40),
                    ),
                    onPressed: onInstall,
                    child: Text(installLabel),
                  ),
                ),
              ],
            ),
          ),
          Positioned(
            top: 0,
            right: 0,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white, size: 20),
              onPressed: onClose,
            ),
          ),
        ],
      ),
    );
  }
}
